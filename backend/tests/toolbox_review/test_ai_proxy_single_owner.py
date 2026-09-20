from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from .conftest import call_tool
from .test_migration_owner_boundaries import _legacy_payload


def test_ai_proxy_and_manual_api_share_one_execution_store(api, tmp_path):
    from backend.ai_mode.agent.tools import ToolExecutor
    from backend.ai_mode.projects import ProjectStore as AiProjectStore
    from backend.toolbox.client import ToolboxClient

    proxy = ToolboxClient(client=api.client)
    facade = AiProjectStore(client=proxy, chat_root=tmp_path / "chat")
    try:
        project = facade.create_project("AI proxy project")
        task = facade.create_task(
            project["id"],
            title="AI proxy task",
            goal="same owner",
            local_workspace=str(api.workspace),
            hpc_workspace="/review/calc",
        )
        manual_projects = api.client.get(
            "/api/v1/toolbox/projects").json()["projects"]
        assert [item["id"] for item in manual_projects] == [project["id"]]

        executor = ToolExecutor(
            store=facade,
            project_id=project["id"],
            task_id=task["id"],
        )
        result = executor.handle("plan", {
            "strategy": "AI via HTTP",
            "jobs": [{
                "key": "relax", "label": "结构优化", "kind": "relax",
                "requires": [],
            }],
        })
        assert result
        manual_detail = api.client.get(
            f"/api/v1/toolbox/projects/{project['id']}/tasks/{task['id']}/detail")
        assert manual_detail.status_code == 200, manual_detail.text
        detail_body = manual_detail.json()
        assert detail_body["flow"]["strategy"] == "AI via HTTP"
        assert detail_body["flow"]["jobs"][0]["key"] == "relax"

        direct = call_tool(api, project["id"], task["id"], "get_state")
        ai_task = facade.get_task(project["id"], task["id"])
        assert ai_task["id"] == task["id"]
        assert ai_task["flow"]["artifacts"] == direct["flow"]["artifacts"]

        execution_path = api.root / "execution_store.json"
        execution_before_chat = execution_path.read_bytes()
        facade.append_message(project["id"], task["id"], "user",
                              "chat belongs only in chat_store")
        assert execution_path.read_bytes() == execution_before_chat
        chat_path = tmp_path / "chat" / "chat_store.json"
        chat_data = json.loads(chat_path.read_text(encoding="utf-8"))
        assert chat_data["messages"][f"{project['id']}:{task['id']}"][0][
            "content"] == "chat belongs only in chat_store"
        assert not (tmp_path / "chat" / "execution_store.json").exists()
    finally:
        facade.close()


def test_ai_server_import_does_not_load_execution_core(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    blocked = (
        "backend.toolbox.api",
        "backend.toolbox.projects",
        "backend.toolbox.service",
        "backend.toolbox.monitor",
        "backend.toolbox.orchestrator",
        "backend.toolbox.ssh",
    )
    code = f"""
import importlib.abc
import sys

blocked = {blocked!r}
class BlockExecutionCore(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == item or fullname.startswith(item + '.') for item in blocked):
            raise RuntimeError('8500 imported execution core: ' + fullname)
        return None

sys.meta_path.insert(0, BlockExecutionCore())
import backend.ai_mode.server
loaded = sorted(name for name in sys.modules
                if any(name == item or name.startswith(item + '.') for item in blocked))
if loaded:
    raise AssertionError(loaded)
print('AI_IMPORT_WITHOUT_EXECUTION_CORE_OK')
"""
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(repo_root),
        VASP_AI_HOME=str(tmp_path / "ai-home"),
        ENABLE_AI_MODE="false",
    )
    env.pop("OPENAI_API_KEY", None)
    env.pop("AI_MODE_LLM_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "AI_IMPORT_WITHOUT_EXECUTION_CORE_OK" in result.stdout


def test_ai_chat_migration_is_read_only_idempotent_and_separate(api, tmp_path):
    from backend.ai_mode.projects import ProjectStore as AiProjectStore
    from backend.toolbox.client import ToolboxClient

    chat_root = tmp_path / "legacy-chat"
    chat_root.mkdir()
    source = chat_root / "ai_store.json"
    source.write_text(json.dumps(_legacy_payload(), ensure_ascii=False, indent=1),
                      encoding="utf-8")
    original = source.read_bytes()
    proxy = ToolboxClient(client=api.client)

    first = AiProjectStore(client=proxy, chat_root=chat_root)
    try:
        messages = first.list_messages("prj_legacy", "tsk_legacy")
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "historical chat")]
    finally:
        first.close()

    assert source.read_bytes() == original
    stored_before_restart = (chat_root / "chat_store.json").read_bytes()
    chat_data = json.loads(stored_before_restart)
    assert "tasks" not in chat_data
    assert "projects" not in chat_data
    assert "flow" not in chat_data
    assert chat_data["context"] == {
        "used": 12, "capacity": 64, "ratio": 0.1875,
    }

    second = AiProjectStore(client=proxy, chat_root=chat_root)
    try:
        assert len(second.list_messages("prj_legacy", "tsk_legacy")) == 1
    finally:
        second.close()
    assert source.read_bytes() == original
    assert (chat_root / "chat_store.json").read_bytes() == stored_before_restart


def test_ai_chat_store_rejects_second_owner_and_recovers_after_close(api, tmp_path):
    from backend.ai_mode.projects import ProjectStore as AiProjectStore
    from backend.toolbox.client import ToolboxClient
    from backend.toolbox.owner import OwnerBusy

    proxy = ToolboxClient(client=api.client)
    chat_root = tmp_path / "single-chat-owner"
    first = AiProjectStore(client=proxy, chat_root=chat_root)
    try:
        with pytest.raises(OwnerBusy):
            AiProjectStore(client=proxy, chat_root=chat_root)
    finally:
        first.close()

    recovered = AiProjectStore(client=proxy, chat_root=chat_root)
    recovered.close()
