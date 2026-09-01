"""M5 决策与授权门测试：风险分级 / 黑名单拒绝 / 挂起卡片 / 批量同意（每作业先检）。"""

from pathlib import Path

import pytest

from ai_mode.authorize import (
    AuthorizationGate,
    ConsentCard,
    ConsentOutcome,
    RiskLevel,
    Verdict,
    VerdictKind,
    confirm as confirm_stateless,
    evaluate as evaluate_stateless,
)
from ai_mode.llm.base import ToolRequest


def _calc(tmp_path) -> Path:
    d = tmp_path / "calc"
    d.mkdir()
    (d / "INCAR").write_text("ENCUT=500\n", encoding="utf-8")
    return d


def _tool(name, **args) -> ToolRequest:
    return ToolRequest(name=name, args=args)


# ---------------- 模型基础 ----------------


def test_risk_level_values():
    assert [r.value for r in RiskLevel] == ["low", "medium", "high"]


def test_verdict_kind_values():
    assert [v.value for v in VerdictKind] == ["allow", "deny", "hold"]


def test_verdict_allowed_property():
    assert Verdict(VerdictKind.ALLOW, risk=RiskLevel.LOW).allowed
    assert not Verdict(VerdictKind.DENY, risk=RiskLevel.HIGH).allowed
    assert not Verdict(VerdictKind.HOLD, risk=RiskLevel.MEDIUM).allowed


def test_consent_card_defaults():
    card = ConsentCard(card_id="c1", tool="write_file", args={"path": "INCAR"})
    assert card.options == ["同意本次", "同意本批", "拒绝"]
    assert card.batch_key == ""
    assert card.risk is RiskLevel.MEDIUM


def test_consent_outcome_fields():
    out = ConsentOutcome(card_id="c1", approved=False, note="n", batch_key="k")
    assert not out.approved
    assert out.note == "n"
    assert out.batch_key == "k"


# ---------------- 风险分级（classify） ----------------


def test_low_risk_tools_allow(tmp_path):
    v = evaluate_stateless(_tool("list_files", path="."), cwd=_calc(tmp_path))
    assert v.kind is VerdictKind.ALLOW
    assert v.risk is RiskLevel.LOW


def test_medium_risk_tools_hold(tmp_path):
    v = evaluate_stateless(_tool("write_file", path="INCAR", content="x"),
                           cwd=_calc(tmp_path))
    assert v.kind is VerdictKind.HOLD
    assert v.risk is RiskLevel.MEDIUM
    assert v.card is not None


def test_unknown_tool_hold(tmp_path):
    v = evaluate_stateless(_tool("teleport", where="there"), cwd=_calc(tmp_path))
    assert v.kind is VerdictKind.HOLD
    assert v.risk is RiskLevel.MEDIUM
    assert "未知工具" in v.reason


def test_submit_tools_always_hold_high(tmp_path):
    d = _calc(tmp_path)
    for name in ("submit_job", "confirm_submit", "sbatch"):
        v = evaluate_stateless(_tool(name, sbatch="run.sh"), cwd=d)
        assert v.kind is VerdictKind.HOLD
        assert v.risk is RiskLevel.HIGH


def test_command_missing_arg_deny_high(tmp_path):
    v = evaluate_stateless(_tool("execute_command", argv=["ls"]), cwd=_calc(tmp_path))
    assert v.kind is VerdictKind.DENY
    assert v.risk is RiskLevel.HIGH
    assert "缺少 command" in v.reason


def test_read_only_command_allow_low(tmp_path):
    v = evaluate_stateless(_tool("execute_command", command="ls"), cwd=_calc(tmp_path))
    assert v.allowed
    assert v.risk is RiskLevel.LOW


def test_dangerous_command_hold_high(tmp_path):
    d = _calc(tmp_path)
    v = evaluate_stateless(_tool("execute_command", command="rm -rf ."), cwd=d)
    assert v.kind is VerdictKind.HOLD
    assert v.risk is RiskLevel.HIGH
    assert v.permits == frozenset({"hold"})


def test_out_of_bounds_read_allow(tmp_path):
    d = _calc(tmp_path)
    v = evaluate_stateless(_tool("execute_command", command="cat ../secret.txt"), cwd=d)
    assert v.kind is VerdictKind.ALLOW
    assert v.risk is RiskLevel.LOW


def test_out_of_bounds_write_hold(tmp_path):
    d = _calc(tmp_path)
    v = evaluate_stateless(_tool("execute_command", command="mkdir -p ../out"), cwd=d)
    assert v.kind is VerdictKind.HOLD
    assert v.risk is RiskLevel.MEDIUM
    assert "out_of_bounds_write" in v.permits


def test_redline_command_deny(tmp_path):
    d = _calc(tmp_path)
    for cmd in ("sudo ls", "scp a.txt b.txt"):
        v = evaluate_stateless(_tool("execute_command", command=cmd), cwd=d)
        assert v.kind is VerdictKind.DENY, cmd
        assert v.risk is RiskLevel.HIGH, cmd


def test_sensitive_path_deny(tmp_path):
    d = _calc(tmp_path)
    v = evaluate_stateless(_tool("execute_command", command="cat ~/.ssh/id_rsa"), cwd=d)
    assert v.kind is VerdictKind.DENY
    assert v.risk is RiskLevel.HIGH


def test_holdable_command_hold(tmp_path):
    d = _calc(tmp_path)
    for cmd in ("curl -o x url", "wget -O x url", "rm -rf out"):
        v = evaluate_stateless(_tool("execute_command", command=cmd), cwd=d)
        assert v.kind is VerdictKind.HOLD, cmd
        assert v.risk is RiskLevel.HIGH, cmd
        assert v.permits == frozenset({"hold"}), cmd


def test_common_commands_allowed(tmp_path):
    d = _calc(tmp_path)
    for cmd in ("cd ..", "pwd", "mkdir -p job1", "mv a b",
                "chmod 700 run.sh", "python run.py", "bash -c echo hi",
                "rm OUTCAR", "grep E INCAR"):
        v = evaluate_stateless(_tool("execute_command", command=cmd), cwd=d)
        assert v.kind is VerdictKind.ALLOW, cmd
        assert v.risk.value in ("low", "medium"), cmd


def test_granted_hold_replays_allow(tmp_path):
    d = _calc(tmp_path)
    tool = _tool("execute_command", command="rm -rf out")
    first = evaluate_stateless(tool, cwd=d)
    assert first.kind is VerdictKind.HOLD
    second = evaluate_stateless(tool, cwd=d, grants=[first.card.batch_key])
    assert second.kind is VerdictKind.ALLOW
    assert second.granted
    assert second.permits == frozenset({"hold"})


def test_denied_hold_not_replayed(tmp_path):
    d = _calc(tmp_path)
    tool = _tool("execute_command", command="rm -rf out")
    first = evaluate_stateless(tool, cwd=d)
    assert first.kind is VerdictKind.HOLD
    second = evaluate_stateless(tool, cwd=d, denials=[first.card.batch_key])
    assert second.kind is VerdictKind.DENY


def test_write_command_allow_medium(tmp_path):
    d = _calc(tmp_path)
    v = evaluate_stateless(_tool("execute_command", command="mkdir -p job1"), cwd=d)
    assert v.kind is VerdictKind.ALLOW
    assert v.risk is RiskLevel.MEDIUM
    v2 = evaluate_stateless(_tool("execute_command", command="ls > listing.txt"), cwd=d)
    assert v2.kind is VerdictKind.ALLOW
    assert v2.risk is RiskLevel.MEDIUM


def test_write_redirect_same_file_denied(tmp_path):
    d = _calc(tmp_path)
    v = evaluate_stateless(_tool("execute_command", command="ls > a.txt 2> a.txt"), cwd=d)
    assert v.kind is VerdictKind.DENY
    assert v.risk is RiskLevel.HIGH


def test_input_redirect_denied(tmp_path):
    d = _calc(tmp_path)
    v = evaluate_stateless(_tool("execute_command", command="cat < INCAR"), cwd=d)
    assert v.kind is VerdictKind.DENY
    assert v.risk is RiskLevel.HIGH


# ---------------- 挂起卡片 ----------------


def test_hold_card_has_options_and_batch_key(tmp_path):
    v = evaluate_stateless(_tool("write_file", path="INCAR"), cwd=_calc(tmp_path))
    assert v.card is not None
    assert v.card.card_id
    assert v.card.batch_key
    assert v.card.options == ["同意本次", "同意本批", "拒绝"]
    assert v.card.args["path"] == "INCAR"


def test_cwd_required_for_command_tools(tmp_path):
    with pytest.raises(ValueError):
        evaluate_stateless(_tool("execute_command", command="ls"), cwd=None)


# ---------------- 批量同意（每作业先检） ----------------


def test_gate_batch_grant_reuses_scope(tmp_path):
    gate = AuthorizationGate(cwd=_calc(tmp_path))
    tool = _tool("write_file", path="INCAR", content="x")
    first = gate.evaluate(tool)
    assert first.kind is VerdictKind.HOLD
    gate.grant([first.card.batch_key])
    second = gate.evaluate(tool)
    assert second.kind is VerdictKind.ALLOW
    assert "本批已同意" in second.reason


def test_gate_confirm_reject(tmp_path):
    gate = AuthorizationGate(cwd=_calc(tmp_path))
    tool = _tool("write_file", path="INCAR", content="x")
    card = gate.evaluate(tool).card
    outcome = gate.confirm(card, choice="拒绝")
    assert not outcome.approved
    assert outcome.note == "用户拒绝"
    assert outcome.batch_key == card.batch_key
    re_eval = gate.evaluate(tool)
    assert re_eval.kind is VerdictKind.HOLD  # 拒绝未记批


def test_gate_confirm_allow_single(tmp_path):
    gate = AuthorizationGate(cwd=_calc(tmp_path))
    tool = _tool("write_file", path="INCAR", content="x")
    card = gate.evaluate(tool).card
    outcome = gate.confirm(card, choice="同意本次")
    assert outcome.approved
    re_eval = gate.evaluate(tool)
    assert re_eval.kind is VerdictKind.HOLD  # 仅本次，不记住整批


def test_gate_confirm_allow_batch(tmp_path):
    gate = AuthorizationGate(cwd=_calc(tmp_path))
    tool = _tool("write_file", path="INCAR", content="x")
    card = gate.evaluate(tool).card
    outcome = gate.confirm(card, choice="同意本批")
    assert outcome.approved
    re_eval = gate.evaluate(tool)
    assert re_eval.kind is VerdictKind.ALLOW
    assert "本批已同意" in re_eval.reason


def test_gate_auto_mode_denies_unapproved(tmp_path):
    gate = AuthorizationGate(cwd=_calc(tmp_path))
    tool = _tool("write_file", path="INCAR", content="x")
    v = gate.evaluate(tool, auto=True)
    assert v.kind is VerdictKind.DENY
    assert "自动模式" in v.reason


def test_gate_auto_mode_still_respects_grant(tmp_path):
    gate = AuthorizationGate(cwd=_calc(tmp_path))
    tool = _tool("write_file", path="INCAR", content="x")
    card = gate.evaluate(tool).card
    gate.grant([card.batch_key])
    v = gate.evaluate(tool, auto=True)
    assert v.kind is VerdictKind.ALLOW


def test_authorize_batch_each_checked(tmp_path):
    gate = AuthorizationGate(cwd=_calc(tmp_path))
    tools = [
        _tool("read_file", path="INCAR"),
        _tool("submit_job", sbatch="run.sh"),
        _tool("write_file", path="INCAR", content="x"),
    ]
    verdicts = gate.authorize_batch(tools)
    kinds = [v.kind for v in verdicts]
    assert kinds[0] is VerdictKind.ALLOW
    assert kinds[1] is VerdictKind.HOLD   # 提交永远挂起（HIGH）
    assert kinds[2] is VerdictKind.HOLD   # 写文件要确认（MEDIUM）
    assert verdicts[1].risk is RiskLevel.HIGH


def test_stateless_evaluate_no_batch_memory(tmp_path):
    d = _calc(tmp_path)
    v1 = evaluate_stateless(_tool("write_file", path="INCAR", content="x"), cwd=d)
    v2 = evaluate_stateless(_tool("write_file", path="INCAR", content="x"), cwd=d)
    assert v1.kind is VerdictKind.HOLD
    assert v2.kind is VerdictKind.HOLD   # 无状态：两次都挂起


def test_stateless_confirm_function(tmp_path):
    card = ConsentCard(card_id="c9", tool="write_file", batch_key="k1")
    out = confirm_stateless(card, choice="同意本批")
    assert out.approved
    assert out.batch_key == "k1"


# ---------------- 敏感路径分级（本地 / 远端） ----------------


def test_local_credential_path_deny_names_token(tmp_path):
    d = _calc(tmp_path)
    for cmd in ("cat ~/.ssh/id_rsa", r"cat C:\Users\alice\.ssh\id_rsa"):
        v = evaluate_stateless(_tool("execute_command", command=cmd), cwd=d)
        assert v.kind is VerdictKind.DENY
        assert v.risk is RiskLevel.HIGH
        assert "凭据" in v.reason
        assert ".ssh" in v.reason


def test_local_system_path_deny(tmp_path):
    d = _calc(tmp_path)
    for cmd in ("cat /etc/hosts", r"cat C:\Windows\win.ini"):
        v = evaluate_stateless(_tool("execute_command", command=cmd), cwd=d)
        assert v.kind is VerdictKind.DENY
        assert "系统敏感路径" in v.reason


def test_local_system_path_deny_names_token(tmp_path):
    d = _calc(tmp_path)
    v = evaluate_stateless(_tool("execute_command", command="cat /etc/passwd"),
                           cwd=d)
    assert v.kind is VerdictKind.DENY
    assert "/etc/passwd" in v.reason


def test_hpc_system_read_allowed():
    from ai_mode.authorize.models import VerdictKind as VK
    from ai_mode.authorize.rules import classify_hpc_command

    risk, kind, reason, permits = classify_hpc_command(
        "cat /etc/hosts", hpc_root="/work/calc")
    assert kind is VK.ALLOW
    assert "只读" in reason


def test_hpc_system_write_denied():
    from ai_mode.authorize.models import VerdictKind as VK
    from ai_mode.authorize.rules import classify_hpc_command

    risk, kind, reason, permits = classify_hpc_command(
        "echo x >> /etc/hosts", hpc_root="/work/calc")
    assert kind is VK.DENY
    assert "系统敏感路径" in reason


def test_hpc_credential_deny_names_token():
    from ai_mode.authorize.models import VerdictKind as VK
    from ai_mode.authorize.rules import classify_hpc_command

    risk, kind, reason, permits = classify_hpc_command(
        "ls ~/.ssh", hpc_root="/work/calc")
    assert kind is VK.DENY
    assert "凭据" in reason
    assert ".ssh" in reason


def test_hpc_out_of_bounds_read_allowed():
    from ai_mode.authorize.models import VerdictKind as VK
    from ai_mode.authorize.rules import classify_hpc_command

    risk, kind, reason, permits = classify_hpc_command(
        "ls /home/alice/elsewhere", hpc_root="/work/calc")
    assert kind is VK.ALLOW


def test_hpc_out_of_bounds_write_hold():
    from ai_mode.authorize.models import VerdictKind as VK
    from ai_mode.authorize.rules import (PERMIT_OUT_OF_BOUNDS_WRITE,
                                         classify_hpc_command)

    risk, kind, reason, permits = classify_hpc_command(
        "mkdir -p /home/alice/elsewhere", hpc_root="/work/calc")
    assert kind is VK.HOLD
    assert PERMIT_OUT_OF_BOUNDS_WRITE in permits
