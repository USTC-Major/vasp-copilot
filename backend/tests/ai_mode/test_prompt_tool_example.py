"""The exact tool example shown to the LLM must round-trip, not just test fixtures."""
import json

from ai_mode.agent.protocol import TOOL_MARK, parse_turn
from ai_mode.agent.runner import build_messages
from ai_mode.projects import ProjectStore


def test_system_tool_example_is_valid_json_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    store = ProjectStore(tmp_path / "home")
    project = store.create_project("prompt contract")
    task = store.create_task(project["id"], goal="read workspace")
    system = build_messages(store, task, [], "只读列出工作区")[0]["content"]
    line = next(line for line in system.splitlines() if line.startswith("- 工具请求："))
    example = line.split("：", 1)[1]
    payload = json.loads(example.removeprefix(TOOL_MARK))
    assert payload["name"] == "ws_list"
    assert payload["args"] == {}
    turn = parse_turn(example)
    assert len(turn.tools) == 1
    assert turn.tools[0].name == "ws_list"
    assert turn.tools[0].args == {}


def test_double_braced_requests_are_still_rejected():
    assert parse_turn(TOOL_MARK + '{{"name":"ws_list","args":{}}}').tools == []
