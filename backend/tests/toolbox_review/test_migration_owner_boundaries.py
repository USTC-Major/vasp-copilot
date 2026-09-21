from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from .conftest import create_isolated_app


def _legacy_payload():
    return {
        "projects": [{
            "id": "prj_legacy",
            "name": "legacy project",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "unknown_project_field": {"preserve": True},
        }],
        "tasks": [{
            "id": "tsk_legacy",
            "project_id": "prj_legacy",
            "title": "legacy task",
            "goal": "keep evidence",
            "status": "running",
            "updated_at": "2026-01-01T00:00:00Z",
            "unknown_task_field": [1, 2, 3],
            "flow": {
                "phase": "monitoring",
                "execution_mode": "Real",
                "plan": {"strategy": "legacy", "jobs": [{
                    "key": "relax",
                    "label": "relax",
                    "kind": "relax",
                    "requires": [],
                    "status": "unknown",
                    "slurm_id": 4815,
                    "submission_state": "unknown",
                    "unknown_job_field": "keep-me",
                }]},
                "consent": {"actions": {
                    "act_pending": {
                        "action_id": "act_pending",
                        "card_id": "act_pending",
                        "state": "pending",
                    },
                    "act_executing": {
                        "action_id": "act_executing",
                        "card_id": "act_executing",
                        "state": "executing",
                    },
                }},
                "unknown_flow_field": {"source": "legacy"},
            },
        }],
        "messages": {"prj_legacy:tsk_legacy": [{
            "role": "user", "content": "historical chat",
        }]},
        "context": {"used": 12, "capacity": 64, "ratio": 0.1875},
        "waiting": [],
        "unknown_root_field": {"preserve": "execution"},
    }


def test_legacy_execution_import_is_read_only_safe_and_idempotent(tmp_path):
    root = tmp_path / "migration-root"
    root.mkdir()
    source = root / "ai_store.json"
    source.write_bytes(json.dumps(
        _legacy_payload(), ensure_ascii=False, indent=1).encode("utf-8"))
    original = source.read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()

    app, _hpc = create_isolated_app(root)
    with TestClient(app) as client:
        projects = client.get("/api/v1/toolbox/projects")
        assert projects.status_code == 200, projects.text
        assert [item["id"] for item in projects.json()["projects"]] == ["prj_legacy"]
        detail = client.get(
            "/api/v1/toolbox/projects/prj_legacy/tasks/tsk_legacy/detail")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["flow"]["jobs"][0]["slurm_id"] == 4815
        assert body["flow"]["jobs"][0]["submission_state"] == "unknown"
        assert body["monitor"]["state"] == "recovering"
        assert body["consents"] == []
        for action_id, expected in {
            "act_pending": "expired",
            "act_executing": "unknown",
        }.items():
            card_response = client.get(
                "/api/v1/toolbox/projects/prj_legacy/tasks/tsk_legacy/"
                f"consents/{action_id}")
            assert card_response.status_code == 200, card_response.text
            assert card_response.json()["card"]["state"] == expected

        created = client.post(
            "/api/v1/toolbox/projects",
            json={"name": "post migration fact"},
        )
        assert created.status_code == 200
        new_id = created.json()["project"]["id"]

    assert source.read_bytes() == original
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    execution_before_restart = (root / "execution_store.json").read_bytes()

    restarted, _hpc = create_isolated_app(root)
    with TestClient(restarted) as client:
        ids = [item["id"] for item in
               client.get("/api/v1/toolbox/projects").json()["projects"]]
        assert set(ids) == {"prj_legacy", new_id}
        assert ids.count("prj_legacy") == 1
        assert ids.count(new_id) == 1

    assert source.read_bytes() == original
    assert (root / "execution_store.json").read_bytes() == execution_before_restart
    execution = json.loads(execution_before_restart)
    assert "messages" not in execution
    assert "context" not in execution
    assert execution["unknown_root_field"] == {"preserve": "execution"}
    imported_project = next(item for item in execution["projects"]
                            if item["id"] == "prj_legacy")
    imported_task = next(item for item in execution["tasks"]
                         if item["id"] == "tsk_legacy")
    assert imported_project["unknown_project_field"] == {"preserve": True}
    assert imported_task["unknown_task_field"] == [1, 2, 3]
    assert imported_task["flow"]["unknown_flow_field"] == {"source": "legacy"}
    assert imported_task["flow"]["plan"]["jobs"][0][
        "unknown_job_field"] == "keep-me"


def test_invalid_legacy_store_is_not_replaced_by_empty_data(tmp_path):
    root = tmp_path / "invalid-migration"
    root.mkdir()
    source = root / "ai_store.json"
    source.write_bytes(b'{"projects": "not-a-list", "tasks": []}')
    original = source.read_bytes()
    app, _hpc = create_isolated_app(root)

    with pytest.raises(ValueError, match="legacy|Invalid"):
        with TestClient(app):
            pass

    assert source.read_bytes() == original
    assert not (root / "execution_store.json").exists()


def test_second_owner_is_rejected_and_release_allows_recovery(tmp_path):
    from backend.toolbox.owner import OwnerBusy, ProcessOwner

    root = tmp_path / "owner-root"
    first = ProcessOwner(root).acquire()
    try:
        with pytest.raises(OwnerBusy):
            ProcessOwner(root).acquire()
    finally:
        first.close()

    recovered = ProcessOwner(root).acquire()
    recovered.close()


def test_owner_lock_is_held_by_process_and_released_on_process_exit(tmp_path):
    from backend.toolbox.owner import OwnerBusy, ProcessOwner

    root = tmp_path / "process-owner-root"
    repo_root = Path(__file__).resolve().parents[3]
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from backend.toolbox.owner import ProcessOwner\n"
        "owner = ProcessOwner(Path(sys.argv[1])).acquire()\n"
        "print('OWNER_READY', flush=True)\n"
        "sys.stdin.readline()\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root)
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(root)],
        cwd=repo_root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "OWNER_READY"
        with pytest.raises(OwnerBusy):
            ProcessOwner(root).acquire()
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=10)

    assert process.returncode == 0, process.stderr.read() if process.stderr else ""
    ProcessOwner(root).acquire().close()


def test_toolbox_source_has_no_ai_mode_or_llm_imports():
    toolbox_root = Path(__file__).resolve().parents[2] / "toolbox"
    violations = []
    for path in toolbox_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                segments = [segment for segment in name.split(".") if segment]
                if "ai_mode" in segments or "llm" in segments:
                    violations.append((path.relative_to(toolbox_root), node.lineno, name))
    assert violations == []
