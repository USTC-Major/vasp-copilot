"""AI conversation adapter. Deterministic execution belongs to Toolbox HTTP.
Text confirmation can request a card; only explicit card approval executes.
"""
from __future__ import annotations

import re
from typing import Callable

from .config import AiModeConfig, load_settings
from .consent import spawn_submit_card as _spawn_submit_card
from .llm.factory import build_client
from .projects import ProjectStore
from .agent.runner import run_agent, run_agent_stream

logger = __import__("logging").getLogger("ai_mode.chat")
__test__ = False  # pytest: 不收集本模块顶层 test_* 函数

#: 计算意图关键词（词、VASP 输入参数、计算类型）。保留给 classify() 兼容导出，
#: 不再作为路由依据（路由已交给 LLM 决策）。
_COMPUTE_KW = (
    "计算", "帮我算", "跑一下", "跑个", "上超算", "提交流程",
    "vasp", "vasprun", "能带", "态密度", "dos", "声子",
    "结构优化", "优化结构", "结构弛豫", "弛豫", "几何优化", "优化",
    "吸附", "缺陷", "表面", "磁性", "分子动力学", "动力学模拟", "aimd",
    "收敛测试", "自洽", "scf", "静态计算",
    "incar", "poscar", "potcar", "kpoints", "k点", "ediff", "encut",
    "ismear", "isif", "ibrion", "nsw", "potim", "lorbit",
    "结构文件", "parchg", "chgcar", "投影", "态密度计算", "能带计算",
)
_COMPUTE_PATTERNS = [re.compile(re.escape(kw), re.IGNORECASE)
                     for kw in _COMPUTE_KW]

_VIEW_SUBSTR = ("看看", "看一下", "查看", "读一下", "工作区有什么", "目录里有什么", "有哪些文件")
Intent = str  # "confirm" | "compute" | "chat"

_PUNCT = str.maketrans("", "", " \t\u3000！!？?。，,、.…·""''「」（）()【】[]（）")


CONFIRM_PHRASES = (
    "开始计算流程", "开始计算", "开始执行", "开始吧",
    "请开始", "走流程", "就这么做", "就这么办",
    "确认开始", "同意开始", "好的",
)
CONFIRM_WORDS = ("好", "嗯", "ok", "yes", "对", "是的", "可以",
                 "开始", "确认", "同意", "没关系")


def classify(content: str) -> Intent:
    """规则意图分流：确认起始 / 计算需求 / 普通聊天。

    仅作兼容导出/调试用途；实际路由已交给 LLM（见 reply/reply_stream）。
    """
    text = _norm(content)
    for phrase in CONFIRM_PHRASES:
        if phrase in text:
            return "confirm"
    if text in CONFIRM_WORDS:
        return "confirm"
    if any(k in text for k in _VIEW_SUBSTR):
        return "chat"
    for pattern in _COMPUTE_PATTERNS:
        if pattern.search(text):
            return "compute"
    return "chat"


def _norm(content: str) -> str:
    return (content or "").strip().lower().translate(_PUNCT)


def _default_llm_factory(_config: AiModeConfig):
    return build_client(_config)






#: spawn_submit_card 已迁至 consent.py（runner 与 chat 共用），此处保留模块级别名。
spawn_submit_card = _spawn_submit_card








def perform_submit(store, project_id, task_id, card_id, approved, note=''):
    from .consent import resolve_card
    return resolve_card(store, project_id, task_id, card_id, approved=approved, note=note).get('result', '')

def _confirmation(store, project_id, task_id, content):
    if _norm(content) not in ('确认提交', '提交', '同意提交'):
        return None
    task = store.get_task(project_id, task_id) or {}
    if (task.get('flow') or {}).get('phase') != 'await_submit':
        return None
    if (task.get('flow') or {}).get('execution_mode') == 'None':
        return 'AI_HPC_BACKEND_UNAVAILABLE：未配置HPC后端，未执行 sbatch'
    result = store.client.tool(project_id, task_id, 'submit', {})
    return str(result.get('result') or '请审核提交授权卡片。')

def reply(store, project_id, task_id, content, *, llm_factory=None, should_stop=None):
    confirmed = _confirmation(store, project_id, task_id, content)
    if confirmed is not None:
        return confirmed
    return run_agent(store, project_id, task_id, content,
                     llm_factory=llm_factory or _default_llm_factory, should_stop=should_stop)

def reply_stream(store, project_id, task_id, content, *, llm_factory=None, should_stop=None):
    confirmed = _confirmation(store, project_id, task_id, content)
    if confirmed is not None:
        yield {'type': 'answer', 'text': confirmed}
        yield {'type': 'done'}
        return
    yield from run_agent_stream(store, project_id, task_id, content,
                               llm_factory=llm_factory or _default_llm_factory, should_stop=should_stop)
