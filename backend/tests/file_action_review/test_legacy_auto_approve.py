"""旧全局免批配置不能绕过逐项上传卡。"""
from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from backend.tests.toolbox_review.conftest import (
    ApiHarness,
    call_tool,
    create_isolated_app,
    create_project_task,
)


@contextmanager
def _upload_setup(tmp_path, *, kinds):
    owner_root = tmp_path / "owner"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "INCAR").write_bytes(b"SYSTEM = si\n")
    app, hpc = create_isolated_app(owner_root, auto_approve_kinds=kinds)
    with TestClient(app) as client:
        api = ApiHarness(client=client, app=app, root=owner_root,
                         workspace=workspace, hpc=hpc)
        project, task = create_project_task(api)
        project_id, task_id = project["id"], task["id"]
        call_tool(api, project_id, task_id, "plan",
                  {"strategy": "one",
                   "jobs": [{"key": "relax", "label": "结构优化", "kind": "relax",
                             "requires": []}]})
        state = call_tool(api, project_id, task_id, "get_state")
        artifact_id = next(aid for aid, value in state["flow"]["artifacts"].items()
                           if value["name"] == "INCAR")
        yield client, api, hpc, project_id, task_id, artifact_id


def test_legacy_upload_auto_approval_still_requires_card(tmp_path):
    with _upload_setup(tmp_path, kinds=("hpc_upload",)) as setup:
        client, api, hpc, project_id, task_id, artifact_id = setup
        result = call_tool(api, project_id, task_id, "hpc_upload",
                           {"artifact_id": artifact_id, "job_key": "relax"})
        assert result["pending"] and result["pending"]["kind"]=='hpc_upload'
        assert result["ok"] is True
        assert "[AUTO_APPROVED]" not in result["result"]
        assert hpc.write_calls == []
        assert "/review/calc/relax/INCAR" not in hpc.files
        # 决议与回执都留在任务记录里，事后可审计
        flow = api.app.state.toolbox.require_task(project_id, task_id)["flow"]
        actions = list(flow["consent"]["actions"].values())
        assert actions and actions[0]["state"] == "pending"
        events = client.get(
            f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/events?after=0"
        ).json()["events"]
        assert not any(event["kind"] == "tool.hpc_upload"
                   and "AUTO_APPROVED" in (event.get("message") or "")
                   for event in events)


def test_upload_still_asks_when_only_other_kinds_are_in_scope(tmp_path):
    with _upload_setup(tmp_path, kinds=("copy_inputs", "generate_kpoints")) as setup:
        _client, api, hpc, project_id, task_id, artifact_id = setup
        result = call_tool(api, project_id, task_id, "hpc_upload",
                           {"artifact_id": artifact_id, "job_key": "relax"})
        assert result["pending"] and result["pending"]["card_id"]
        assert hpc.write_calls == []
