from __future__ import annotations

from fastapi.testclient import TestClient

from backend.tests.toolbox_review.conftest import (
    ApiHarness,
    call_tool,
    create_isolated_app,
    create_project_task,
)


def test_legacy_upload_local_source_change_fails_before_any_remote_write(tmp_path):
    owner_root = tmp_path / "owner"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "INCAR"
    source.write_text("SYSTEM = original\n", encoding="utf-8")
    app, hpc = create_isolated_app(owner_root)
    with TestClient(app) as client:
        api = ApiHarness(
            client=client,
            app=app,
            root=owner_root,
            workspace=workspace,
            hpc=hpc,
        )
        project, task = create_project_task(api)
        project_id, task_id = project["id"], task["id"]
        planned = call_tool(
            api,
            project_id,
            task_id,
            "plan",
            {
                "strategy": "one",
                "jobs": [
                    {
                        "key": "job",
                        "label": "job",
                        "kind": "static",
                        "requires": [],
                    }
                ],
            },
        )
        assert planned["flow"]["jobs"][0]["status"] == "draft"
        state = call_tool(api, project_id, task_id, "get_state")
        artifact_id = next(
            key
            for key, value in state["flow"]["artifacts"].items()
            if value["name"] == "INCAR"
        )
        proposal = call_tool(
            api,
            project_id,
            task_id,
            "hpc_upload",
            {"artifact_id": artifact_id, "job_key": "job"},
        )
        card = proposal["pending"]
        source.write_text("SYSTEM = changed!\n", encoding="utf-8")
        response = client.post(
            f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/consents/{card['card_id']}",
            json={"approved": True, "note": "source changed after proposal"},
        )
        assert response.status_code == 200, response.text
        saved = response.json()["card"]
        assert saved["state"] == "failed"
        assert "file_dispatch_at" not in saved
        assert hpc.write_calls == []
        assert not any(command.startswith("mkdir -p") for command, _ in hpc.run_calls)
