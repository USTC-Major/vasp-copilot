"""Explicit adapters for historical tests that combine agent and core fixtures."""
import pytest

@pytest.fixture(autouse=True)
def legacy_agent_test_transport(request, monkeypatch):
    if request.module.__name__.split('.')[-1] in {
        'test_agent', 'test_chat', 'test_mp_tools', 'test_receipt_completion',
        'test_prompt_tool_example', 'test_stream_recovery',
    }:
        import ai_mode.agent.runner as runner
        from backend.tests.toolbox.legacy_bridge import make_executor
        monkeypatch.setattr(runner, '_make_executor', make_executor)
