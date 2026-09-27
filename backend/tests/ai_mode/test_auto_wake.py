"""事件驱动自动唤醒：什么时候该替用户"催"AI，什么时候不要打扰。"""
from backend.ai_mode.auto_wake import wake_prompt


def _detail(jobs, *, phase="monitoring", cards=()):
    return {"flow": {"phase": phase,
                     "jobs": [{"key": key, "status": status} for key, status in jobs]},
            "consents": list(cards)}


def test_no_wake_when_nothing_meaningful_happened():
    detail = _detail([("relax", "completed"), ("relax/static", "draft")],
                     phase="monitoring")
    assert wake_prompt(detail, [], phase="monitoring") is not None  # 有终态作业 → 值得唤醒
    quiet = _detail([("relax", "running")], phase="monitoring")
    assert wake_prompt(quiet, [], phase="monitoring") is None


def test_wake_on_pending_submit_card_and_on_failure():
    card = {"kind": "submit", "state": "pending"}
    detail = _detail([("relax", "completed"), ("relax/static", "draft")], cards=[card])
    prompt = wake_prompt(detail, [], phase="monitoring")
    assert prompt and "待确认的提交卡" in prompt
    failed = [{"kind": "consent.failed", "message": "[ROOT_CHANGED] 身份变化"}]
    prompt = wake_prompt(_detail([("relax", "running")]), failed, phase="monitoring")
    assert prompt and "刚出现失败" in prompt


def test_wake_prompt_tells_the_model_to_self_heal_and_not_ask_the_user():
    detail = _detail([("relax", "completed"), ("relax/static/dos", "draft")],
                     cards=[{"kind": "submit", "state": "pending"}])
    prompt = wake_prompt(detail, [], phase="monitoring") or ""
    assert "自己判断下一步并直接执行到底" in prompt
    assert "不要停下来问用户" in prompt
    assert "点确认即可" in prompt
