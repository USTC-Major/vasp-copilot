"""授权卡片 ↔ 任务流程的中转层（M47）。

卡片 / 同意 / 拒绝状态持久化在 ``task.flow.consent``：
    {
      "cards":   {card_id: CardPayload},
      "grants":  [batch_key, ...],
      "denials": [batch_key, ...],
    }

- 工具（run_exec / hpc_exec）命中 HOLD 时：生成卡片并抛 PendingConsentError 暂停本轮对话；
- 用户在 UI 点「同意/拒绝」→ ``POST /messages/consent`` 调 resolve_card() 写入 grants/denials；
- 续跑时工具用 grants/denials 重新评估：granted -> ALLOW+permits 带提权执行；denied -> 不再弹卡。
- 真实提交确认卡片（confirm_submit）由 chat 层生成，同意后直接驱动 orchestrator 提交（红线不变）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "PendingConsentError",
    "consent_of",
    "list_cards",
    "get_card",
    "save_card",
    "resolve_card",
    "grants_of",
    "denials_of",
    "card_payload",
    "spawn_submit_card",
]

_CARDS_KEY = "cards"
_GRANTS_KEY = "grants"
_DENIALS_KEY = "denials"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def consent_of(flow: dict) -> dict:
    """归一化读取 flow['consent'] 缺省字典。"""
    cons = flow.get("consent")
    if not isinstance(cons, dict):
        cons = {}
    cons.setdefault(_CARDS_KEY, {})
    cons.setdefault(_GRANTS_KEY, [])
    cons.setdefault(_DENIALS_KEY, [])
    return cons


def _save_flow(store, project_id: str, task_id: str, flow: dict,
               cons: dict) -> None:
    flow["consent"] = cons
    flow["updated_at"] = _now_iso()
    store.update_task(project_id, task_id, flow=dict(flow),
                      status=(flow.get("status") or store.get_task(project_id,
                                                                   task_id)
                              or {}).get("status", "planned"))


def list_cards(store, project_id: str, task_id: str) -> list[dict]:
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    cons = consent_of(flow)
    return list(cons.get(_CARDS_KEY, {}).values())


def get_card(store, project_id: str, task_id: str,
             card_id: str) -> dict | None:
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    cons = consent_of(flow)
    return cons.get(_CARDS_KEY, {}).get(card_id)


def card_payload(*, tool: str, args: dict, risk: str, reason: str,
                 batch_key: str, kind: str, summary: str,
                 options: list[str] | None = None) -> dict:
    """构造给前端/事件的卡片载荷（不含时间，保持可预测）。"""
    return {
        "card_id": uuid.uuid4().hex,
        "tool": tool,
        "args": dict(args or {}),
        "risk": risk,
        "reason": reason,
        "options": options or ["同意本次", "同意本批", "拒绝"],
        "batch_key": batch_key,
        "kind": kind,          # workspace（本地/远端操作提权） | submit（提交确认）
        "summary": summary,
    }


def save_card(store, project_id: str, task_id: str, flow: dict,
              payload: dict) -> dict:
    """把一张卡片写入任务 flow.consent 并返回卡片。"""
    load = consent_of(flow)
    # 同 batch_key 的旧卡先清（不重复弹卡）
    cards = {cid: c for cid, c in load[_CARDS_KEY].items()
             if c.get("batch_key") != payload.get("batch_key")}
    payload.setdefault("at", _now_iso())
    cards[payload["card_id"]] = payload
    load[_CARDS_KEY] = cards
    _save_flow(store, project_id, task_id, flow, load)
    return payload


def _key_lists(cons: dict) -> tuple[list[str], list[str]]:
    return (cons.get(_GRANTS_KEY, []), cons.get(_DENIALS_KEY, []))


def grants_of(store, project_id: str, task_id: str) -> list[str]:
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    return list(consent_of(flow).get(_GRANTS_KEY, []))


def denials_of(store, project_id: str, task_id: str) -> list[str]:
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    return list(consent_of(flow).get(_DENIALS_KEY, []))


def resolve_card(store, project_id: str, task_id: str, card_id: str, *,
                 approved: bool, note: str = "") -> dict:
    """处理一张卡片并落库：同意 -> 记入 grants；拒绝 -> 记入 denials；同时移除该卡。

    返回 (approved, batch_key, tool, note) 相关信息字典。
    """
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    cons = consent_of(flow)
    cards = cons[_CARDS_KEY]
    card = cards.get(card_id)
    if card is None:
        return {"approved": approved, "batch_key": "", "tool": "",
                "note": note or "", "missing": True}
    batch_key = card.get("batch_key") or ""
    tool = card.get("tool") or ""
    if approved:
        if batch_key and batch_key not in cons[_GRANTS_KEY]:
            cons[_GRANTS_KEY].append(batch_key)
    else:
        if batch_key and batch_key not in cons[_DENIALS_KEY]:
            cons[_DENIALS_KEY].append(batch_key)
    cards.pop(card_id, None)
    _save_flow(store, project_id, task_id, flow, cons)
    return {"approved": approved, "batch_key": batch_key, "tool": tool,
            "note": note or "", "missing": False, "card_id": card_id}


class PendingConsentError(Exception):
    """工具执行中命中 HOLD：带着已持久化的卡片中止本轮对话，等待用户同意/拒绝。"""

    def __init__(self, card: dict):
        self.card = card
        self.card_id = card.get("card_id", "")
        self.batch_key = card.get("batch_key", "")
        self.tool = card.get("tool", "")
        super().__init__(card.get("reason") or "该操作需要你的授权")


def _stable_key(text: str) -> str:
    """生成稳定作用域键（同一批草稿/远端目录只弹一张提交卡）。"""
    return uuid.uuid5(uuid.NAMESPACE_URL, str(text)).hex


def spawn_submit_card(store, project_id: str, task_id: str) -> dict:
    """await_submit 环节 -> 生成「确认提交」授权卡片。

    只有用户通过卡片点「确认提交」才真正驱动 sbatch；「取消」则把流程置为已取消。
    """
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    jobs = (flow.get("plan") or {}).get("jobs") or []
    active = [j for j in jobs
              if j.get("status") not in
              ("completed", "failed", "canceled", "skipped")]
    dir_by_job: dict[str, str] = {}
    for d in flow.get("draft") or []:
        if isinstance(d, dict) and d.get("job_key") and d.get("dir"):
            dir_by_job[str(d["job_key"])] = str(d["dir"])
    lines = "\n".join(
        f"- {j.get('key')}（{j.get('label') or j.get('key')}）"
        + (f"→ `{dir_by_job[j.get('key')]}`"
           if j.get("key") in dir_by_job else "")
        for j in active) or "（无待提交作业）"
    remote = (flow.get("hpc_dir") or flow.get("local_dir") or "").strip()
    drafts = sorted(str(d) for d in flow.get("draft") or [])
    sig = _stable_key(f"{remote}|{drafts}")
    payload = card_payload(
        tool="confirm_submit", args={},
        risk="high",
        reason=("这是真实的提交动作：确认后系统才会把草稿写入目标目录并执行 sbatch。"
                "提交前请先核对作业内容与目标工作区。"),
        batch_key=f"submit|{sig}",
        kind="submit",
        summary=f"确认提交到超算工作区 `{remote}`？\n{lines}",
        options=["确认提交", "取消"],
    )
    save_card(store, project_id, task_id, dict(flow), payload)
    return payload