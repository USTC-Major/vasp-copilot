"""上传免批范围：开启后由 Toolbox 直接执行并留回执，关闭时照旧逐项弹卡。"""
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


def test_upload_runs_without_a_card_when_in_scope(tmp_path):
    with _upload_setup(tmp_path, kinds=("hpc_upload",)) as setup:
        client, api, hpc, project_id, task_id, artifact_id = setup
        result = call_tool(api, project_id, task_id, "hpc_upload",
                           {"artifact_id": artifact_id, "job_key": "relax"})
        assert result["pending"] is None
        assert result["ok"] is True
        assert "[AUTO_APPROVED]" in result["result"], result["result"]
        assert hpc.write_calls == ["/review/calc/relax/INCAR"]
        assert hpc.files["/review/calc/relax/INCAR"] == b"SYSTEM = si\n"
        # 决议与回执都留在任务记录里，事后可审计
        flow = api.app.state.toolbox.require_task(project_id, task_id)["flow"]
        actions = list(flow["consent"]["actions"].values())
        assert actions and actions[0]["state"] == "executed"
        assert actions[0]["note"].startswith("按智能设置中开启的免批范围自动批准")
        assert "已把这份输入上传到超算工作区" in actions[0]["result"]
        events = client.get(
            f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/events?after=0"
        ).json()["events"]
        assert any(event["kind"] == "tool.hpc_upload"
                   and "AUTO_APPROVED" in (event.get("message") or "")
                   for event in events)


def test_upload_still_asks_when_only_other_kinds_are_in_scope(tmp_path):
    with _upload_setup(tmp_path, kinds=("copy_inputs", "generate_kpoints")) as setup:
        _client, api, hpc, project_id, task_id, artifact_id = setup
        result = call_tool(api, project_id, task_id, "hpc_upload",
                           {"artifact_id": artifact_id, "job_key": "relax"})
        assert result["pending"] and result["pending"]["card_id"]
        assert hpc.write_calls == []
