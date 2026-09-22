from __future__ import annotations
from .config import load_settings
from .projects import ProjectStore

def perform_submit(store: ProjectStore, project_id: str, task_id: str,
                   card_id: str, approved: bool, note: str = "", *, orch=None) -> str:
    """Serialize one complete submit decision with all other task mutations."""
    from .consent import task_lock

    with task_lock(project_id, task_id):
        return _perform_submit_locked(store, project_id, task_id, card_id,
                                      approved, note, orch=orch)


def _perform_submit_locked(store: ProjectStore, project_id: str, task_id: str,
                           card_id: str, approved: bool, note: str = "", *, orch=None) -> str:
    """Resolve and claim a single-use submit action before any scheduler call."""
    from .consent import claim_action, finish_action, get_card, resolve_card
    from .orchestrator import Orchestrator

    card = get_card(store, project_id, task_id, card_id)
    if card is None:
        return "确认卡片不存在或已处理，请重新发起确认。"
    resolved = resolve_card(store, project_id, task_id, card_id,
                            approved=approved, note=note)
    if resolved.get("conflict") or resolved.get("expired") or resolved.get("tampered"):
        return "该确认已处理、过期或失效；不会重复提交。"
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    orch = orch or Orchestrator.from_settings(load_settings())
    if approved:
        action = claim_action(store, project_id, task_id, card_id)
        if action is None:
            return "该确认已失效或已被使用；不会重复提交。"
        # Claiming is persisted before any scheduler call.  Reload so every
        # orchestrator save carries the authoritative ``executing`` action
        # instead of overwriting it with the pre-claim snapshot.
        flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
        binding = action.get("binding") or {}
        current_mode = str(getattr(orch, "execution_mode",
                                   flow.get("execution_mode") or "None"))
        from .computation import scope_valid
        if (not scope_valid(flow, action)
                or binding.get("project_id") != project_id
                or binding.get("task_id") != task_id
                or binding.get("remote_root") != str(flow.get("hpc_dir") or flow.get("local_dir") or "").strip()
                or binding.get("execution_mode") != current_mode):
            result = "提交目标或草稿在确认后发生变化；已拒绝执行，sbatch=0。"
            finish_action(store, project_id, task_id, card_id,
                          state="failed", result=result)
            return result
        try:
            result = orch._submit(store, project_id, task_id, dict(flow))
        except Exception as exc:
            finish_action(store, project_id, task_id, card_id,
                          state="unknown",
                          result=f"提交结果不确定且不会自动重试：{type(exc).__name__}")
            raise
        current = (store.get_task(project_id, task_id) or {}).get("flow") or {}
        uncertain = any(
            job.get("submission_action_id") == card_id and job.get("submission_state") == "unknown"
            for job in ((current.get("plan") or {}).get("jobs") or []))
        submitted = any(
            job.get("submission_action_id") == card_id
            and job.get("submission_state") == "submitted"
            for job in ((current.get("plan") or {}).get("jobs") or []))
        finish_action(store, project_id, task_id, card_id,
                      state=("unknown" if uncertain else
                             "executed" if submitted else "failed"),
                      result=result)
        return result
    action = claim_action(store, project_id, task_id, card_id)
    if action is not None:
        # Defensive only: rejected cards cannot normally be claimed.
        finish_action(store, project_id, task_id, card_id,
                      state="failed", result="提交已取消")
    return "已取消本次单计算提交；其他计算保持原状态。"
