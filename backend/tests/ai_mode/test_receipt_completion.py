"""Completed operations awaiting user input must not trigger another LLM call."""
import pytest

from ai_mode.agent import run_agent, run_agent_stream
from ai_mode.agent.runner import _receipt_stall, _strip_receipt_wait
from ai_mode.config import AiModeConfig
from ai_mode.llm.fake import FakeLLM
from ai_mode.projects import ProjectStore


@pytest.mark.parametrize("text", [
    "INCAR已按回执上传。等待你下一步指令。",
    "工具回执显示上传完成；未继续上传其他文件。",
    "根据回执，操作已完成，等待用户确认下一步。",
    "已核验回执，不再执行其他操作。",
])
def test_completed_receipt_is_preserved(text):
    assert not _receipt_stall(text)
    assert _strip_receipt_wait(text) == text


@pytest.mark.parametrize("text", [
    "收到回执后继续。",
    "等工具回执后再继续。",
    "等待回执。",
    "稍后收到工具回执再推进。",
])
def test_future_receipt_wait_still_detected(text):
    assert _receipt_stall(text)
    assert _strip_receipt_wait(text) == ""


@pytest.mark.parametrize("streaming", [False, True])
def test_completed_reply_ends_without_extra_llm_call(tmp_path, monkeypatch, streaming):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    store = ProjectStore(tmp_path / "home")
    project = store.create_project("completion regression")
    task = store.create_task(project["id"], goal="核对上传结果")
    llm = FakeLLM()
    text = "INCAR已按回执上传。等待你下一步指令。"
    llm.enqueue(text)
    llm.enqueue("错误的额外调用，不应发生。")
    cfg = AiModeConfig(data_dir=tmp_path / "data")
    args = (store, project["id"], task["id"], "核对已上传文件")
    if streaming:
        events = list(run_agent_stream(*args, cfg=cfg, llm_factory=lambda c: llm))
        assert events[-1]["type"] == "done"
        answer = events[-1]["answer"]
    else:
        answer = run_agent(*args, cfg=cfg, llm_factory=lambda c: llm)
    assert answer == text
    assert len(llm.calls) == 1
