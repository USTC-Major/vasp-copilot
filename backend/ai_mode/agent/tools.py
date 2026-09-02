# -*- coding: utf-8 -*-
"""agent 工具集（M31）：把真实操作暴露给 LLM 决策调用，全程过安全门。

设计原则（对齐安全边界 + 产品红线）：
- 每条工具请求都过 ``AuthorizationGate``（再 + rules 分级），拒绝/白名单由策略兜底。
- 写操作只允许落在本任务计算目录（local_dir）内；读取只限本地工作区/计算目录。
- ``submit`` 只把流程停在「待确认」，绝不代替用户执行 sbatch —— 真实提交由
  orchestrator 在用户明确「确认提交」后执行（红线上移，LLM 不触碰）。
- 所有工具只返回文本回执给 LLM，不返回密钥/口令，不做网络外带。
"""
from __future__ import annotations

import json
import logging
import posixpath
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ..authorize.gatekeeper import AuthorizationGate
from ..authorize.models import VerdictKind
from ..authorize.rules import (PERMIT_HOLD, PERMIT_OUT_OF_BOUNDS_WRITE,
                               classify_hpc_command)
from ..consent import (PendingConsentError, card_payload, denials_of,
                       grants_of, save_card)
from ..config import AiModeConfig, load_settings
from ..exec.errors import ExecutionPolicyViolation
from ..exec.policy import check_path_in_bounds
from ..exec.runner import run_command
from ..llm.base import ToolRequest
from ..projects import ProjectStore
from ..schemas import PlanSnapshot, PlanStep
from ..tools.draft import (find_remote_submit_script,
                           resolve_user_submit_script, submit_command)
from ..workflow.plan import validate_plan
from ..workspace import snapshot_hpc_workspace, snapshot_workspace

logger = logging.getLogger("ai_mode.agent.tools")
__test__ = False

#: handle() 捕获 PendingConsentError，返回 _CONSENT_PENDING+card_id；runner 据此 yield card 事件。
_CONSENT_PENDING = "__CONSENT_PENDING__"

#: flow.phase -> 任务展示状态（与 orchestrator 对齐）
_PHASE_STATUS = {
    "running": "planned",
    "await_submit": "generated",
    "monitoring": "submitted",
    "done": "done",
    "blocked": "planned",
}

_WRITE_INPUT_CAP = 200_000       # write_input 单文件内容上限
_WS_READ_CAP = 12000             # ws_read 单文件预览上限
_HPC_READ_CAP = 12000            # hpc_read 单文件预览上限
_HPC_UPLOAD_CAP = 64 * 1024 * 1024   # hpc_upload 单文件大小上限（64 MB）


#: 作业 key/命名的语义化（M46）：r1/s1 等晦涩 key 归一为可读英文语义名。
_OPAQUE_KEY_RE = re.compile(r"^(r|s|q|job|step|task)?[0-9]*$",
                            re.IGNORECASE)

#: kind -> 默认中文作业类型名（LLM 未写 label 时兜底）
_KIND_LABELS = {
    "relax": "结构优化",
    "opt": "结构优化",
    "structure": "结构优化",
    "static": "静态自洽",
    "scf": "静态自洽",
    "scf_calculation": "静态自洽",
    "band": "能带计算",
    "bands": "能带计算",
    "bandstructure": "能带计算",
    "band_structure": "能带计算",
    "dos": "态密度计算",
    "density": "态密度计算",
    "density_of_states": "态密度计算",
    "phonon": "声子计算",
    "aimd": "分子动力学",
    "md": "分子动力学",
    "molecular_dynamics": "分子动力学",
}

#: 中文作业类型名 -> 可读英文语义 key（LLM 习惯给 r1/s1 之类晦涩 key 时替换）
_LABEL_SLUGS = {
    "结构优化": "relax",
    "静态自洽": "static",
    "能带计算": "band",
    "态密度计算": "dos",
    "声子计算": "phonon",
    "分子动力学": "aimd",
}

#: 用户跳过/已终态的作业，draft/submit 均不再触碰
_TERMINAL_SKIP = ("completed", "failed", "canceled", "skipped")


def _semantic_label_for_kind(kind: str) -> str:
    return _KIND_LABELS.get((kind or "").strip().lower(), "VASP 计算")


def _label_slug(label: str) -> str:
    """中文作业类型名 -> 英文语义 key（无映射时 ASCII 清洗兜底）。"""
    slug = _LABEL_SLUGS.get((label or "").strip())
    if slug:
        return slug
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", (label or "").strip()).strip("_")
    return cleaned.lower() or "calc"


def _unique_key(base: str, used: set) -> str:
    if base not in used:
        return base
    i = 2
    while f"{base}_{i}" in used:
        i += 1
    return f"{base}_{i}"


def _canon_job_key(raw_key: str, label: str, used: set) -> str:
    """把作业 key 归一为可读语义名：晦涩 key（r1/s1/job1/step2）按 label 生成。

    M52：保留 ``/`` 表达嵌套作业目录（依赖链依次往下建，如 relax/static），
    每段独立清洗；``..``/空段/隐藏段被丢弃（写入侧 _clean_job_subdir 仍会拦截）。
    """
    raw = (raw_key or "").strip().replace("\\", "/")
    if raw and not _OPAQUE_KEY_RE.match(raw):
        segs: list[str] = []
        for seg in raw.split("/"):
            seg = re.sub(r"[^A-Za-z0-9_.-]+", "_", seg).strip("_")
            if seg and seg not in (".", "..") and not seg.startswith("."):
                segs.append(seg)
        base = "/".join(segs) if segs else "calc"
    else:
        base = _label_slug(label)
    return _unique_key(base, used)


def _canon_ref(raw: str) -> str:
    """requires 引用清洗（与 _canon_job_key 同规则；不查 used、不按 label 兜底）。"""
    raw = (raw or "").strip().replace("\\", "/")
    segs: list[str] = []
    for seg in raw.split("/"):
        seg = re.sub(r"[^A-Za-z0-9_.-]+", "_", seg).strip("_")
        if seg and seg not in (".", "..") and not seg.startswith("."):
            segs.append(seg)
    return "/".join(segs)

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def tool_schema_text() -> str:
    """给 LLM 的工具说明（prompt 内使用的文本 schema）。"""
    return (
        "- get_state：查看当前计算流程状态（phase/规划/作业/precheck/草稿）。args: {}\n"
        "- ws_list：列出任务本地工作区文件（只读快照，有界）。args: {}\n"
        "- ws_read：读取本地工作区某个文件全文（只读、有界）。args: {\"path\":\"相对路径\"}\n"
        "- hpc_list：列出超算工作区（hpc_dir）远端目录内容（只读；计算发生地，超算上的文件一律用它看）。args: {\"path\":\"相对子目录，可空\"}\n"
        "- hpc_read：读取超算工作区内某个文本文件（只读、有界）。args: {\"path\":\"相对路径\"}\n"
        "- hpc_upload：把本地工作区文件上传到超算工作区（SFTP 只写 hpc_dir 内；每次需用户弹卡授权；禁止用 scp，安全策略会直接拒绝）。args: {\"source\":\"本地工作区内相对路径\",\"dest\":\"远端相对路径，可省略=同 source\"}\n"
        "- hpc_write_script：把提交脚本（*.sh）写入超算作业目录——仅在本地与远端都没有用户脚本时使用，每次需用户弹卡授权（本地绝不写 *.sh）。args: {\"dir\":\"作业子目录，可空\",\"filename\":\"sub_vasp.sh\",\"content\":\"脚本文本\"}\n"
        "- run_exec：在本地计算目录执行一条命令（受安全策略约束；目录外只读放行，破坏性/解释器/安装/下载等高风险须你弹卡授权；红线命令仍拒）。args: {\"command\":\"命令\"}\n"
        "- hpc_exec：在指定超算工作区（hpc_dir）内执行一条远端命令。查看目录/文件请优先用 hpc_list/hpc_read；本工具用于它们覆盖不了的命令（如 squeue/module/vaspkit）。同样受策略约束；红线拒绝，目录外/高风险需弹卡授权。args: {\"command\":\"命令\"}\n"
        "- stop_monitor：终止当前计算流程（用户明确表示不做了/换思路/作业作废时调用）：全部未完成作业置 canceled、停止后台监控与自动补提；已在超算运行的作业会给出 scancel 建议。args: {}\n"
        "- plan：自主制定计算计划并落库（作业数/类型/顺序由你决定）。作业 key 请用语义化英文名（如 relax/static/band/dos），label 用中文。有先后依赖的作业必须用 requires 声明依赖（如 {\"key\":\"relax/static\",\"requires\":[\"relax\"]}），系统会等前序 completed 后自动补提后续；依赖链作业 key/目录用嵌套路径依次往下建（relax → relax/static → relax/static/dos），独立作业才并列。args: {\"strategy\":\"策略\",\"jobs\":[{\"key\":\"relax\",\"label\":\"结构优化\",\"kind\":\"relax\"}]}\n"
        "- write_input：把内容写入本地计算目录文件（INCAR/KPOINTS/POTCAR 等输入，限目录内；**不能写 *.sh 提交脚本，它必须由用户自己提供**）。每个作业独占一个子目录（目录名=作业 key，支持嵌套路径如 relax/static），多作业时务必带 dir 指明该作业目录。args: {\"filename\":\"INCAR\",\"content\":\"...\",\"dir\":\"relax\"}\n"
        "- copy_inputs：从本地工作区把输入文件复制到计算目录（多作业时带 dir 指明该作业子目录，支持嵌套路径）。args: {\"filenames\":[\"POSCAR\",\"INCAR\"],\"dir\":\"relax\"}\n"
        "- precheck：对计算目录做提交前检查（告知性、不阻塞，由你判断；按作业目录逐项检查；其中提交脚本 *.sh 必须由用户提供唯一脚本，缺失会标 [error]）。args: {\"required\":[\"INCAR\",\"POSCAR\",\"KPOINTS\",\"POTCAR\"]}\n"
        "- draft：为已规划作业生成提交草稿并停在「待你确认提交」（只校验、不执行 sbatch）。提交脚本优先用超算作业目录里已有的唯一 *.sh（超算为主），其次本地计算目录；两边都没有则停在阻塞态——此时你可起草脚本用 hpc_write_script 经用户弹卡同意后写入超算。args: {}\n"
        "- submit：把流程停在「待你确认提交」边界（同样不会代替用户执行；真实提交由系统在用户确认后执行）。args: {}\n"
        "- select_jobs：按用户要求选择本次提交哪些作业/跳过哪些（只调规划不提交；跳过作业不生成草稿也不提交）。args: {\"submit\":[\"relax\"],\"skip\":[\"static\"]}\n"
        "- monitor：查询超算作业进度（squeue 实况 + 状态推进；未连接超算如实说明）。args: {}\n"
        "- report：作业全部终态后生成结果报告（从 OUTCAR/OSZICAR 提取真报告）。args: {}"
    )


class ToolExecutor:
    """在一个计算任务内执行 LLM 请求的工具。每个消息由调用方新建实例。"""

    def __init__(self, *, store: ProjectStore, project_id: str, task_id: str,
                 cfg: Optional[AiModeConfig] = None,
                 orch: Optional[Any] = None,
                 orch_factory: Optional[Callable[[], Any]] = None,
                 should_stop: Optional[Callable[[], bool]] = None):
        self.store = store
        self.project_id = project_id
        self.task_id = task_id
        self.cfg = cfg or load_settings()
        self._orch = orch
        self._orch_factory = orch_factory
        self.should_stop = should_stop

    # ---------------- 流程 / 目录 ----------------
    def _task(self) -> dict:
        return self.store.get_task(self.project_id, self.task_id) or {}

    def local_dir(self) -> Path:
        ws = (self._task().get("local_workspace") or "").strip()
        if ws:
            return Path(ws).expanduser().resolve()
        return self.cfg.data_dir / "workspace" / f"{self.project_id}__{self.task_id}"

    def _job_target_dir(self, key: str) -> Optional[Path]:
        """作业子目录：<key> 目录已存在时返回它，否则返回 None（表示用计算目录根）。"""
        key = str(key or "").strip()
        if not key:
            return None
        cand = self.local_dir() / key
        return cand if cand.is_dir() else None

    @staticmethod
    def _clean_job_subdir(raw) -> Optional[str]:
        """write_input/copy_inputs/hpc_upload/hpc_write_script 的 dir 参数：

        空串=根目录；单段名=子目录；多段相对路径=嵌套作业目录（M52，
        如 relax/static）；绝对路径/盘符/``..``/空段/隐藏段非法返回 None。
        """
        text = str(raw or "").strip().replace("\\", "/")
        if not text or text == ".":
            return ""
        if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
            return None
        segs: list[str] = []
        for seg in text.split("/"):
            seg = seg.strip()
            if (not seg or seg == ".." or seg.startswith(".")
                    or re.search(r'[<>:"|?*\x00-\x1f]', seg)):
                return None
            segs.append(seg)
        return "/".join(segs)

    def _validate_job_dir(self, sub: str) -> str:
        """M54：作业目录白名单——已规划多作业时，非空 dir 必须是某个作业
        的完整嵌套 key；否则拒绝并列出合法目录（防 AI 自创 relax/dos 这类
        变体目录）。未规划/单作业或空 dir（写共享文件到根）时放行。"""
        if not sub:
            return ""
        try:
            flow = self._load_flow() or {}
            jobs = ((flow.get("plan") or {}).get("jobs")) or []
        except Exception:  # noqa: BLE001
            return ""
        if len(jobs) < 2:
            return ""
        keys = [str(j.get("key") or "") for j in jobs
                if isinstance(j, dict) and j.get("key")]
        if sub in keys or not keys:
            return ""
        return (f"非法 dir：`{sub}` 不是任何已规划作业的目录。"
                f"合法作业目录：{'、'.join(keys)}"
                "（依赖链作业必须用规划时的完整嵌套 key，"
                "如 relax/static/dos；不要自创 relax/dos 等变体）")

    def _load_flow(self) -> dict:
        task = self._task()
        flow = task.get("flow") or {}
        return dict(flow)   # 每次从 store 重新读，避免跨消息旧状态

    def _save_flow(self, flow: dict) -> None:
        flow = dict(flow)
        flow["updated_at"] = _now_iso()
        self.store.update_task(self.project_id, self.task_id,
                               flow=flow,
                               status=_PHASE_STATUS.get(flow.get("phase"),
                                                        "planned"))

    def _ensure_orch(self):
        if self._orch is None and self._orch_factory is not None:
            self._orch = self._orch_factory()
        if self._orch is None:
            from ..orchestrator import Orchestrator
            self._orch = Orchestrator.from_settings(self.cfg)
        return self._orch

    def phase(self) -> str:
        return self._load_flow().get("phase") or ""

    def auto_pump(self) -> str:
        """监控态下自动推进一次真实 squeue 状态（保证进度不丢），返回实况文本。"""
        try:
            return self.tool_monitor({})
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动进度查询失败: %s", exc)
            return f"（自动进度查询失败：{type(exc).__name__}）"

    # ---------------- 分发 ----------------
    def handle(self, name: str, args: dict) -> str:
        """执行一个工具并返回给 LLM 的回执文本；任何异常都不会中断决策循环。"""
        func = getattr(self, "tool_" + str(name).strip().lower(), None)
        if func is None:
            return (f"未知工具：{name}（可用工具见系统提示；请改正工具名后重试，"
                    "或者改用 get_state 查看当前状态）")
        try:
            return func(args or {})
        except PendingConsentError as exc:
            logger.info("工具 %s 命中授权卡片 %s", name, exc.card_id)
            return _CONSENT_PENDING + exc.card_id
        except ExecutionPolicyViolation as exc:
            return f"安全策略拒绝：{getattr(exc, 'reason', str(exc))}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("工具 %s 执行失败: %s", name, exc)
            return f"工具 {name} 执行失败：{type(exc).__name__}（{exc}）"

    # ---------------- 只读 / 查询 ----------------
    def tool_get_state(self, args: dict) -> str:
        flow = self._load_flow()
        plan = flow.get("plan") or {}
        jobs = plan.get("jobs") or []
        job_lines = "\n".join(
            f"- {j.get('key')}（{j.get('label') or ''}，{j.get('kind') or 'vasp'}）"
            f" status={j.get('status') or 'draft'}"
            + (f" slurm_id={j.get('slurm_id')}" if j.get("slurm_id") else "")
            for j in jobs) or "（暂无规划）"
        drafts = flow.get("draft") or []
        draft_names = "、".join(
            (f"{d.get('job_key')}:{d.get('script_name')}"
             + (f"@{d.get('dir')}" if d.get("dir") else ""))
            for d in drafts) or "（暂无）"
        pre = flow.get("precheck") or {}
        issue_n = len(pre.get("issues") or [])
        pre_text = "ok" if pre.get("ok") else (f"{issue_n} 项问题" if issue_n else "未检查")
        return (
            "【当前计算流程状态】\n"
            f"- 阶段 phase：{flow.get('phase') or '（未开始）'}\n"
            f"- 目标 goal：{flow.get('goal') or self._task().get('goal') or '（未填写）'}\n"
            f"- 规划 strategy：{plan.get('strategy') or '（未规划）'}\n{job_lines}\n"
            f"- 本地计算目录：{flow.get('local_dir') or self.local_dir()}\n"
            f"- 超算目录 hpc_dir：{flow.get('hpc_dir') or '（未设置）'}\n"
            f"- 提交前检查：{pre_text}\n"
            f"- 提交草稿：{draft_names}\n"
            f"- 已上传超算：{'是' if flow.get('uploaded') else '否'}"
        )

    def tool_ws_list(self, args: dict) -> str:
        root = self._task().get("local_workspace") or ""
        _found, text = snapshot_workspace(root)
        return text

    def tool_ws_read(self, args: dict) -> str:
        rel = str(args.get("path") or "").strip()
        if not rel:
            return "缺少参数 path（本地工作区内的相对路径）"
        root = self._task().get("local_workspace") or ""
        if not root:
            return "任务未设置本地工作区（local_workspace）"
        rootp = Path(root).expanduser().resolve()
        try:
            target = check_path_in_bounds(rel, rootp, write=False)
        except ExecutionPolicyViolation as exc:
            return f"安全策略拒绝：{getattr(exc, 'reason', str(exc))}"
        if not target.is_file():
            return f"文件不存在：{rel}"
        try:
            data = target.read_bytes()
        except OSError as exc:
            return f"读取失败：{type(exc).__name__}"
        text = data.decode("utf-8", "replace")
        if len(text) > _WS_READ_CAP:
            text = text[:_WS_READ_CAP] + "\n…（文件过长已截断）"
        return f"--- 文件 {rel} ---\n{text}"

    def tool_precheck(self, args: dict) -> str:
        required = args.get("required")
        if not isinstance(required, list) or not required:
            required = ["INCAR", "POSCAR", "KPOINTS", "POTCAR"]
        local_dir = self.local_dir()
        rows: list[str] = []
        issues: list[dict] = []
        ok = True
        flow = self._load_flow()
        jobs = (flow.get("plan") or {}).get("jobs") or []
        if not jobs:
            return "尚未规划作业：请先调用 plan 再 precheck"
        remote = (self._hpc_root(flow) or "").rstrip("/")
        hpc = None
        try:
            hpc = getattr(self._ensure_orch(), "hpc", None)
        except Exception:  # noqa: BLE001
            hpc = None
        for job in jobs:
            key = job["key"]
            job_dir = self._job_target_dir(key)
            base = job_dir if job_dir is not None else local_dir
            suffix = (f"（作业目录 {key}）" if job_dir is not None else "")
            calc = (self._remote_job_dir(hpc, remote, key)
                    if (hpc is not None and remote) else "")
            for name in required:
                name = str(name or "").strip()
                if not name:
                    continue
                # M51 超算优先：先查超算，本地兑底，标注来源
                exists, where = False, ""
                if calc:
                    stat = getattr(hpc, "stat", None)
                    try:
                        if stat and stat(f"{calc}/{name}") is not None:
                            exists, where = True, "（超算）"
                    except Exception:  # noqa: BLE001
                        pass
                if not exists and (base / name).is_file():
                    exists, where = True, "（本地）"
                if exists:
                    rows.append(f"- [ok] {name} 存在{where}{suffix}")
                else:
                    ok = False
                    rows.append(f"- [warn] {name} 缺失"
                                "（告知性提示，是否补齐由你判断）" + suffix)
                    issues.append({"job": key, "file": name, "level": "warn",
                                   "message": f"{name} 缺失{suffix}"})
            # 提交脚本：超算作业目录优先，本地回退（M51 超算为主）
            script_ok = False
            if calc:
                try:
                    rname = find_remote_submit_script(hpc, calc)
                    if rname:
                        script_ok = True
                        rows.append(f"- [ok] 提交脚本 {rname} 存在（超算）"
                                    f"{suffix}（用户提供）")
                except RuntimeError as exc:
                    rows.append(f"- [error] {exc}{suffix}")
                    ok = False
                    issues.append({"job": key, "file": "提交脚本(*.sh)",
                                   "level": "error", "message": str(exc)})
                    script_ok = True  # 已报错，不再重复报本地缺失
            if not script_ok:
                try:
                    resolve_user_submit_script(base)
                    rows.append(f"- [ok] 提交脚本(*.sh) 存在（本地）{suffix}"
                                "（用户提供）")
                except RuntimeError as exc:
                    ok = False
                    rows.append(f"- [error] {exc}；本地与超算都没有——可把脚本"
                                f"放进任一目录，或由我起草后经你弹卡同意写入超算"
                                f"（hpc_write_script）{suffix}")
                    issues.append({"job": key, "file": "提交脚本(*.sh)",
                                   "level": "error", "message": str(exc)})
        flow["precheck"] = {"ok": ok, "issues": issues}
        self._save_flow(flow)
        return "提交前检查（告知性、不阻塞；由你决定是否继续）：\n" + "\n".join(rows)

    # ---------------- 执行 / 写入（均过安全门） ----------------
    # ---------------- 授权评估 / 卡片（M47：弹卡=申请提权） ----------------
    def _eval(self, name: str, args: dict, cwd: str, *, hpc: bool = False):
        grants = grants_of(self.store, self.project_id, self.task_id)
        denials = denials_of(self.store, self.project_id, self.task_id)
        if not hpc:
            gate = AuthorizationGate(cwd=cwd)
        else:
            from ..authorize.rules import classify as _default_classify

            def _classify(tool: ToolRequest, *, cwd):
                if str(tool.name or "").strip().lower() == "hpc_exec":
                    risk, kind, reason, permits = classify_hpc_command(
                        str((tool.args or {}).get("command") or ""),
                        hpc_root=cwd)
                    return risk, kind, reason, None, permits
                return _default_classify(tool, cwd=cwd)

            gate = AuthorizationGate(cwd=cwd, classify_callable=_classify)
        return gate.evaluate(ToolRequest(name, dict(args)), cwd=cwd,
                             auto=False, grants=grants, denials=denials)

    def _consent_card(self, verdict, *, tool: str, args: dict, kind: str,
                      summary: str) -> dict:
        opts = verdict.card.options if verdict.card else None
        return card_payload(
            tool=tool, args=dict(args or {}), risk=verdict.risk.value,
            reason=verdict.reason,
            batch_key=(verdict.card.batch_key if verdict.card else ""),
            kind=kind, summary=summary, options=opts)

    def tool_run_exec(self, args: dict) -> str:
        command = str(args.get("command") or args.get("cmd") or "").strip()
        if not command:
            return "缺少参数 command"
        cwd = self.local_dir()
        verdict = self._eval("run_exec", {"command": command}, str(cwd))
        if verdict.kind is VerdictKind.DENY:
            return f"安全策略拒绝执行：{verdict.reason}"
        if verdict.kind is VerdictKind.HOLD:
            payload = self._consent_card(
                verdict, tool="run_exec", args={"command": command},
                kind="workspace",
                summary=("我将执行本地命令（高风险，需你同意后才执行）：\n"
                         f"$ {command}\n执行位置：本地计算目录。{verdict.reason}"),
            )
            save_card(self.store, self.project_id, self.task_id,
                      self._load_flow(), payload)
            raise PendingConsentError(payload)
        cwd.mkdir(parents=True, exist_ok=True)
        result = run_command(
            command, cwd=str(cwd),
            permit_hold=(PERMIT_HOLD in verdict.permits),
            permit_out_of_bounds_write=(
                PERMIT_OUT_OF_BOUNDS_WRITE in verdict.permits),
            should_stop=self.should_stop,
        )
        return result.summary(800)

    def tool_hpc_exec(self, args: dict) -> str:
        command = str(args.get("command") or args.get("cmd") or "").strip()
        if not command:
            return "缺少参数 command"
        orch = self._ensure_orch()
        hpc = getattr(orch, "hpc", None)
        if hpc is None:
            return ("未连接超算（未配置 SSH 主机/账号），无法执行远端命令；"
                    "请到「设置 → SSH」填写主机/用户名/口令后重试。")
        flow = self._load_flow()
        hpc_root = self._hpc_root(flow)
        if not hpc_root:
            return ("任务未设置超算工作区（hpc_dir），无法锚定远端执行目录；"
                    "请先在任务里填写超算工作区。")
        verdict = self._eval("hpc_exec", {"command": command}, hpc_root,
                             hpc=True)
        if verdict.kind is VerdictKind.DENY:
            return f"安全策略拒绝远端执行：{verdict.reason}"
        if verdict.kind is VerdictKind.HOLD:
            payload = self._consent_card(
                verdict, tool="hpc_exec", args={"command": command},
                kind="workspace",
                summary=("我将执行超算命令（高风险，需你同意后才执行）：\n"
                         f"$ {command}\n执行位置：超算工作区 {hpc_root}。"
                         f"{verdict.reason}"),
            )
            save_card(self.store, self.project_id, self.task_id, flow, payload)
            raise PendingConsentError(payload)
        if self.should_stop is not None and self.should_stop():
            return "远端命令已被用户停止（未执行）"
        try:
            _code, out, err = hpc.run(command, cwd=hpc_root)
        except Exception as exc:  # noqa: BLE001
            return f"远端命令执行失败：{type(exc).__name__}（{exc}）"
        if "vaspkit" in command.lower():
            # M57：vaspkit 运行会留下 *.err/*.log 临时文件，用户政策是不保留，
            # 执行完顺手清理（限工作区内、best-effort，失败不影响主命令回执）。
            try:
                hpc.run("find . -maxdepth 4 \\( -name '*.err' -o -name '*.log' \\) "
                        "-delete", cwd=hpc_root)
            except Exception:  # noqa: BLE001
                pass
        text = (out or "").strip()
        if (err or "").strip():
            text = (text + "\n[stderr]\n" + (err or "").strip()).strip()
        if len(text) > 6000:
            text = text[:3600] + "\n…（输出过长已截断）\n" + text[-1800:]
        return f"远端命令已执行\n输出：{text}"

    # ---------------- 本地 -> 超算受限上传（SFTP，非 scp） ----------------
    def tool_hpc_upload(self, args: dict) -> str:
        """把本地工作区文件上传到超算工作区（SFTP 只写 hpc_dir 内；免卡）。

        安全边界：source 限本地工作区内相对路径（check_path_in_bounds 校验）；
        dest 限 hpc_dir 内相对路径（词法 + normpath 防越界）；单文件大小上限
        _HPC_UPLOAD_CAP；绝不执行 scp/rsync 等外带命令（红线）。
        M57 用户政策：上传免弹卡（通道本身受控，逐文件弹卡太累），
        除提交作业外其余操作尽量不打扰用户。
        """
        src_rel = str(args.get("source") or "").strip().replace("\\", "/")
        if not src_rel:
            return "缺少参数 source（本地工作区内的相对路径）"
        dest_rel = (str(args.get("dest") or "").strip().replace("\\", "/")
                    or src_rel)
        hpc, root, err = self._hpc_ready()
        if err:
            return err
        # 词法裁决（DENY 直接拒；HOLD 免卡放行——通道本身已受控）
        verdict = self._eval("hpc_upload",
                             {"source": src_rel, "dest": dest_rel}, root,
                             hpc=True)
        if verdict.kind is VerdictKind.DENY:
            return f"安全策略拒绝上传：{verdict.reason}"
        # 本地侧校验（存在性/越界/大小）
        try:
            target = check_path_in_bounds(src_rel, self.local_dir(),
                                          write=False)
        except ExecutionPolicyViolation as exc:
            return f"安全策略拒绝：{getattr(exc, 'reason', str(exc))}"
        if not target.is_file():
            return f"本地工作区不存在文件：{src_rel}"
        try:
            data = target.read_bytes()
        except OSError as exc:
            return f"读取本地文件失败：{type(exc).__name__}"
        if len(data) > _HPC_UPLOAD_CAP:
            return (f"文件过大（{len(data)} B > 上限 {_HPC_UPLOAD_CAP} B），"
                    "拒绝上传；请压缩或拆分后再试。")
        # M57：免卡直接上传（HOLD 不再弹卡）
        dest = f"{root.rstrip('/')}/{posixpath.normpath(dest_rel)}"
        parent = posixpath.dirname(dest)
        try:
            if parent:
                quoted = "'" + parent.replace("'", "'\\''") + "'"
                hpc.run(f"mkdir -p {quoted}")
            hpc.write_file(dest, data)
        except Exception as exc:  # noqa: BLE001
            return f"上传失败：{type(exc).__name__}（{exc}）"
        return (f"已上传 {src_rel}（{len(data)} B）→ 超算 {dest}（经 SFTP）。")

    # ---------------- 提交脚本写入超算（*.sh；M57 政策：免弹卡） ------
    def tool_hpc_write_script(self, args: dict) -> str:
        """把 AI 起草的提交脚本（*.sh）写入超算作业目录（免弹卡，直接写入）。

        用户政策（M57 取代 M51）：除 sbatch 提交外其余操作不再弹卡，
        写脚本也直接执行；本地仍绝不写 *.sh（write_input 拒收不变）。
        """
        content = str(args.get("content") or "")
        if not content.strip():
            return "缺少参数 content（提交脚本文本）"
        filename = (str(args.get("filename") or "sub_vasp.sh").strip()
                    .replace("\\", "/"))
        if "/" in filename or not filename.lower().endswith(".sh"):
            return ("非法 filename：只允许在超算作业目录下写 *.sh 文件"
                    "（如 sub_vasp.sh）；本地绝不写 *.sh。")
        sub = self._clean_job_subdir(args.get("dir"))
        if sub is None:
            return "非法 dir（仅允许计算目录内的相对路径，如 relax 或 relax/static）"
        bad_dir = self._validate_job_dir(sub)
        if bad_dir:
            return bad_dir
        hpc, root, err = self._hpc_ready()
        if err:
            return err
        dest_dir = f"{root.rstrip('/')}/{sub}" if sub else root
        dest = f"{dest_dir.rstrip('/')}/{filename}"
        # 授权评估：DENY（红线，如越界写）仍拒绝；HOLD 免卡放行
        verdict = self._eval("hpc_write_script",
                             {"dir": sub, "filename": filename,
                              "content": content}, root,
                             hpc=True)
        if verdict.kind is VerdictKind.DENY:
            return f"安全策略拒绝：{verdict.reason}"
        # 写入远端（M57：免弹卡）
        try:
            quoted = "'" + dest_dir.replace("'", "'\\''") + "'"
            hpc.run(f"mkdir -p {quoted}")
            hpc.write_file(dest, content.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return f"写入失败：{type(exc).__name__}（{exc}）"
        return (f"已把提交脚本写入超算 `{dest}`（{len(content)} 字符）。"
                "后续 draft 将直接使用该脚本。")

    @staticmethod
    def _remote_job_dir(hpc, base: str, key: str) -> str:
        """远端作业目录：远端存在 <base>/<key> 子目录则用之，否则 base
        （对齐 Orchestrator._job_calc_dir 的扁平回退语义）。"""
        stat = getattr(hpc, "stat", None)
        if stat is not None and key:
            try:
                if stat(f"{base.rstrip('/')}/{key}") is not None:
                    return f"{base.rstrip('/')}/{key}"
            except Exception:  # noqa: BLE001
                pass
        return base

    # ---------------- 超算工作区只读查看（与本地 ws_* 对应） ----------------
    def _hpc_root(self, flow: dict | None = None) -> str:
        """任务超算工作区根目录：优先 flow.hpc_dir，回退 task.hpc_workspace。"""
        flow = flow if flow is not None else self._load_flow()
        return ((flow.get("hpc_dir") or "").strip()
                or (self._task().get("hpc_workspace") or "").strip())

    def _hpc_ready(self) -> tuple[Any, str, str]:
        """返回 (hpc, hpc_root, err)；err 非 None 时 hpc/root 无意义。"""
        orch = self._ensure_orch()
        hpc = getattr(orch, "hpc", None)
        if hpc is None:
            return None, "", ("未连接超算（未配置 SSH 主机/账号），无法查看超算"
                            "工作区；请到「设置 → SSH」填写主机/用户名/口令后重试。")
        root = self._hpc_root()
        if not root:
            return None, "", ("任务未设置超算工作区（hpc_dir），无法定位远端目录；"
                            "请先在任务里填写超算工作区。")
        return hpc, root, ""

    @staticmethod
    def _hpc_rel(args: dict) -> Optional[str]:
        """解析远端相对路径参数；空返回 \"\"，非法返回 None（防越界）。"""
        rel = str(args.get("path") or "").strip().replace("\\", "/")
        if not rel or rel == ".":
            return ""
        if rel.startswith("/") or ":" in rel:
            return None
        norm = posixpath.normpath(rel)
        if norm == ".." or norm.startswith("../") or norm.startswith("/"):
            return None
        return norm

    def tool_hpc_list(self, args: dict) -> str:
        """列出超算工作区目录内容（只读，SFTP；远端对应的 ws_list）。"""
        hpc, root, err = self._hpc_ready()
        if err:
            return err
        rel = self._hpc_rel(args)
        if rel is None:
            return "非法 path（仅允许 hpc_dir 内相对路径）"
        target = f"{root.rstrip('/')}/{rel}" if rel else root
        try:
            infos = hpc.list_dir_info(target)
        except Exception as exc:  # noqa: BLE001
            return f"列目录失败：{type(exc).__name__}（{target}）"
        lines: list[str] = []
        for info in infos:
            name = str(info.get("name") or "")
            if not name:
                continue
            if info.get("is_dir"):
                lines.append(f"- {name}/（目录）")
            else:
                lines.append(f"- {name}（{int(info.get('size') or 0)} B）")
        body = "\n".join(lines) if lines else "（空目录）"
        return f"【超算目录】{target}\n{body}"

    def tool_hpc_read(self, args: dict) -> str:
        """读超算工作区内某个文本文件全文（只读、有界；远端对应的 ws_read）。"""
        hpc, root, err = self._hpc_ready()
        if err:
            return err
        rel = self._hpc_rel(args)
        if rel is None:
            return "非法 path（仅允许 hpc_dir 内相对路径）"
        if not rel:
            return "缺少参数 path（hpc_dir 内的相对路径）"
        target = f"{root.rstrip('/')}/{rel}"
        try:
            data = bytes(hpc.read_file(target,
                                       max_bytes=_HPC_READ_CAP * 2))
        except Exception as exc:  # noqa: BLE001
            return f"读取失败：{type(exc).__name__}（{target}）"
        text = data.decode("utf-8", "replace")
        if len(text) > _HPC_READ_CAP:
            text = text[:_HPC_READ_CAP] + "\n…（文件过长已截断）"
        return f"--- 超算文件 {rel} ---\n{text}"

    def hpc_snapshot(self) -> str:
        """超算工作区紧凑快照（给系统提示注入用）；不可用/异常返回空串。"""
        try:
            hpc, root, err = self._hpc_ready()
            if err:
                return ""
            _found, text = snapshot_hpc_workspace(hpc, root)
            return text
        except Exception:  # noqa: BLE001
            logger.warning("超算工作区快照生成失败", exc_info=True)
            return ""

    def tool_plan(self, args: dict) -> str:
        strategy = str(args.get("strategy") or "").strip()
        jobs = args.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            return "plan 需要非空 jobs 数组（[{key,label,kind}]）；请先理解需求再规划"
        goal = str(args.get("goal") or "").strip() or \
            (self._task().get("goal") or "")
        local_dir = self.local_dir()
        local_dir.mkdir(parents=True, exist_ok=True)
        flow = self._load_flow()
        normalized: list[dict] = []
        used: set = set()
        for j in jobs:
            if not isinstance(j, dict):
                continue
            kind = str(j.get("kind") or "vasp").strip() or "vasp"
            label = str(j.get("label") or "").strip() or \
                _semantic_label_for_kind(kind)
            key = _canon_job_key(str(j.get("key") or ""), label, used)
            used.add(key)
            normalized.append({
                "key": key,
                "label": label,
                "kind": kind,
                "description": str(j.get("description") or "")[:200],
                "requires": list(j.get("requires") or []),
                "status": "draft",
                "slurm_id": None,
            })
        if not normalized:
            return "plan jobs 中没有有效条目"
        # M52：requires 引用归一化到规范化 key，并校验依赖合法性（未知/自依赖/成环）
        keys = {j["key"] for j in normalized}
        for j in normalized:
            refs: list[str] = []
            for req in j["requires"]:
                if req in keys:
                    refs.append(req)
                    continue
                canon = _canon_ref(req)
                refs.append(canon if canon in keys else req)
            j["requires"] = refs
        issues = validate_plan(PlanSnapshot(steps=[
            PlanStep(job_key=j["key"], label=j["label"],
                     requires=j["requires"]) for j in normalized]))
        if issues:
            return ("规划不合法：" + "；".join(issues)
                    + "（可用作业键：" + "、".join(sorted(keys)) + "）"
                    + "；请修正 requires 后重新 plan")
        flow.update({
            "phase": "running",
            "goal": goal,
            "plan": {"strategy": strategy or "（LLM 自主规划）", "jobs": normalized},
            "local_dir": str(local_dir),
        })
        flow.setdefault("hpc_dir", str(self._task().get("hpc_workspace") or "").strip())
        flow["precheck"] = {"ok": False, "issues": []}
        flow.setdefault("draft", [])
        flow.setdefault("uploaded", False)
        flow.setdefault("waiting", [])
        flow.setdefault("extractions", {})
        flow.setdefault("report", "")
        flow.setdefault("logs", [])
        flow.setdefault("started_at", _now_iso())
        self._save_flow(flow)
        labels = "；".join(f"{j['key']}（{j['label']}，{j['kind']}）" for j in normalized)
        return f"已规划 {len(normalized)} 条作业：{labels}。策略：{strategy or '（未写）'}"

    def tool_write_input(self, args: dict) -> str:
        filename = str(args.get("filename") or "").strip()
        content = args.get("content") or ""
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)
        content = str(content)
        sub = self._clean_job_subdir(args.get("dir"))
        if sub is None:
            return "非法 dir（仅允许计算目录内的相对路径，如 relax 或 relax/static）"
        bad_dir = self._validate_job_dir(sub)
        if bad_dir:
            return bad_dir
        if not filename:
            return "缺少参数 filename"
        if filename.startswith("/") or "\\" in filename or ".." in filename \
                or "#" in filename:
            return f"非法文件名：{filename}"
        if filename.lower().endswith(".sh"):
            return ("拒绝写入提交脚本：*.sh 提交脚本必须由用户自己提供，"
                    "系统/AI 不代写。请把唯一的 *.sh 放进对应作业目录。")
        if len(content) > _WRITE_INPUT_CAP:
            content = content[:_WRITE_INPUT_CAP] + "\n…（内容过长已截断）"
        local_dir = self.local_dir()
        target_dir = (local_dir / sub) if sub else local_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            target = check_path_in_bounds(target_dir / filename, local_dir,
                                          write=True)
        except ExecutionPolicyViolation as exc:
            return f"安全策略拒绝：{getattr(exc, 'reason', str(exc))}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"写入失败：{type(exc).__name__}"
        where = f"作业目录 `{sub}`/" if sub else "计算目录 "
        return f"已写入{where} {filename}（{len(content)} 字符）。"

    def tool_copy_inputs(self, args: dict) -> str:
        filenames = args.get("filenames")
        if not isinstance(filenames, list):
            return "需要参数 filenames（字符串数组）"
        source = self._task().get("local_workspace") or ""
        if not source:
            return "任务未设置本地工作区，无法复制输入文件"
        src_root = Path(source).expanduser().resolve()
        local_dir = self.local_dir()
        sub = self._clean_job_subdir(args.get("dir"))
        if sub is None:
            return "非法 dir（仅允许计算目录内的相对路径，如 relax 或 relax/static）"
        bad_dir = self._validate_job_dir(sub)
        if bad_dir:
            return bad_dir
        target_dir = (local_dir / sub) if sub else local_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        same_root = src_root == target_dir.expanduser().resolve()
        copied: list[str] = []
        missing: list[str] = []
        for name in filenames:
            name = str(name or "").strip()
            if not name or name.startswith("/") or "\\" in name or ".." in name:
                missing.append(name)
                continue
            try:
                src = check_path_in_bounds(src_root / name, src_root, write=False)
            except ExecutionPolicyViolation:
                missing.append(name)
                continue
            if src.is_file():
                try:
                    if same_root:
                        copied.append(name)
                    else:
                        shutil.copy2(src, target_dir / name)
                        copied.append(name)
                except OSError:
                    missing.append(name)
            else:
                missing.append(name)
        where = f"作业目录 `{sub}`" if sub else "计算目录"
        msg = f"已复制到{where}：{('、'.join(copied) if copied else '（无）')}"
        if missing:
            msg += f"；缺失或非法：{'、'.join(missing)}"
        return msg

    # ---------------- 提交边界（不代替用户真实提交） ----------------
    def tool_draft(self, args: dict) -> str:
        flow = self._load_flow()
        jobs = (flow.get("plan") or {}).get("jobs") or []
        if not jobs:
            return "尚未规划作业：请先调用 plan 再 draft（参考 get_state）"
        remote = (self._hpc_root(flow) or "").rstrip("/")
        local_dir = self.local_dir()
        hpc = None
        try:
            hpc = getattr(self._ensure_orch(), "hpc", None)
        except Exception:  # noqa: BLE001
            hpc = None
        drafts: list[dict] = []
        lines: list[str] = []
        active = [j for j in jobs
                  if j.get("status") not in _TERMINAL_SKIP]
        if not active:
            return ("全部作业均已被跳过或缺终态，无需生成草稿；"
                    "可用 select_jobs 重新选择后再 draft。")
        missing: list[str] = []
        # （job, job_local, calc_dir, script_name, source）——source: remote/local
        resolved: list[tuple] = []
        for job in active:
            job_local = ((local_dir / job["key"])
                         if (local_dir / job["key"]).is_dir() else local_dir)
            calc_dir = str(job_local)   # 本地回退：作业子目录（无超算时）
            script_name = None
            source = "local"
            if hpc is not None and remote:
                calc_dir = self._remote_job_dir(hpc, remote, job["key"])
                try:
                    script_name = find_remote_submit_script(hpc, calc_dir)
                except RuntimeError as exc:
                    missing.append(f"- {job['key']}（{job.get('label') or job['key']}）："
                                   f"{exc}")
                    continue
                if script_name:
                    source = "remote"
            if not script_name:
                try:
                    script_name = resolve_user_submit_script(job_local).name
                except RuntimeError as exc:
                    missing.append(f"- {job['key']}（{job.get('label') or job['key']}）："
                                   f"{exc}")
                    continue
            resolved.append((job, job_local, calc_dir, script_name, source))
        if missing:
            flow["phase"] = "blocked"
            self._save_flow(flow)
            return ("无法生成提交草稿：缺少提交脚本（*.sh）。"
                    "脚本放哪里都可以：超算作业目录（优先）或本地计算目录；"
                    "也可以由我起草脚本经你弹卡同意后写入超算"
                    "（hpc_write_script）。系统绝不在未经用户同意的情况下"
                    "代写或写入提交脚本：\n"
                    + "\n".join(missing))
        for job, job_local, calc_dir, script_name, source in resolved:
            where = "本地计算目录"
            if source == "remote":
                where = "超算作业目录"
                try:
                    raw = hpc.read_file(f"{calc_dir.rstrip('/')}/{script_name}",
                                        max_bytes=20000)
                    script_text = bytes(raw).decode("utf-8", "replace")
                except Exception as exc:  # noqa: BLE001
                    flow["phase"] = "blocked"
                    self._save_flow(flow)
                    return (f"远端提交脚本读取失败（{calc_dir}/{script_name}）："
                            f"{type(exc).__name__}；请检查后重试。")
            else:
                script = resolve_user_submit_script(job_local)
                script_text = script.read_text(encoding="utf-8")
            drafts.append({
                "job_key": job["key"],
                "dir": calc_dir,
                "script_name": script_name,
                "script_text": script_text,
                "script_source": source,
                "submit_cmd": " ".join(submit_command(script_name)),
            })
            lines.append(f"- {job['key']}（{job.get('label') or job['key']}）"
                         f"→ 目录 `{calc_dir}`，使用{where}的提交脚本 "
                         f"{script_name}（提交命令 "
                         f"{submit_command(script_name)[0]} {script_name}）")
        flow["draft"] = drafts
        flow["phase"] = "await_submit"
        self._save_flow(flow)
        skipped = [j["key"] for j in jobs
                   if j.get("status") in ("canceled", "skipped")]
        suffix = ("（已跳过：" + "、".join(skipped) + "）") if skipped else ""
        return ("已生成提交草稿（使用用户提供的提交脚本，只校验、未提交）"
                + suffix + "：\n"
                + "\n".join(lines)
                + "\n\n真实提交需用户明确确认：请告知用户回复「确认提交」后由系统提交到"
                  "超算；「取消」则放弃。本次未执行任何 sbatch，也未代写任何脚本。")

    def tool_submit(self, args: dict) -> str:
        flow = self._load_flow()
        if not flow.get("draft"):
            if (flow.get("plan") or {}).get("jobs"):
                return self.tool_draft(args)
            return "尚未生成提交草稿：请先 plan + draft"
        flow["phase"] = "await_submit"
        self._save_flow(flow)
        return ("已停在「待你确认提交」（红线：我绝不代替你执行 sbatch）。"
                "请用户回复「确认提交」→ 系统真实提交到超算；「取消」→ 放弃。"
                "系统会在每个作业自己的目录里执行 sbatch"
                "（输入文件与提交脚本同目录，绝不在此目录的上一级发起提交）。"
                "未连接超算时系统会如实说明。")

    # ---------------- 作业层级干预（选择提交/跳过，只调规划不提交） ----------------
    def tool_select_jobs(self, args: dict) -> str:
        """按用户要求选择「本次提交哪些作业/跳过哪些」，只调整规划，不提交。"""
        flow = self._load_flow()
        jobs = (flow.get("plan") or {}).get("jobs") or []
        if not jobs:
            return "尚未规划作业：请先调用 plan 再使用 select_jobs（参考 get_state）"
        submit = [str(x).strip().lower() for x in (args.get("submit") or [])]
        skip = [str(x).strip().lower() for x in (args.get("skip") or [])]
        submit_all = bool(args.get("submit_all"))
        skip_all = bool(args.get("skip_all"))
        if not submit and not skip and not submit_all and not skip_all:
            return ("select_jobs 需要给出选择：提交用 {\"submit\":[\"relax\"]}，"
                    "跳过用 {\"skip\":[\"static\"]}，或 {\"submit_all\":true} / "
                    "{\"skip_all\":true}")

        def _matches(job: dict, terms: list[str]) -> bool:
            key = str(job.get("key") or "").strip().lower()
            label = str(job.get("label") or "").strip().lower()
            for t in terms:
                if t == key or t == label or key in t or t in label:
                    return True
            return False

        for job in jobs:
            if skip_all or (skip and _matches(job, skip)):
                job["status"] = "skipped"
            elif submit_all or _matches(job, submit):
                if job.get("status") in ("skipped", "canceled"):
                    job["status"] = "draft"
        flow["draft"] = []      # 选择变化后旧草稿作废，等待重新 draft
        self._save_flow(flow)
        to_submit = [str(j.get("key")) + "（" + str(j.get("label")) + "）"
                     for j in jobs
                     if j.get("status") not in _TERMINAL_SKIP]
        skipped = [str(j.get("key")) + "（" + str(j.get("label")) + "）"
                   for j in jobs
                   if j.get("status") in ("skipped", "canceled")]
        return ("已按选择调整作业（仅规划，未提交任何东西）：\n"
                + "- 本次提交：" + ("、".join(to_submit) or "（无）") + "\n"
                + "- 跳过：" + ("、".join(skipped) or "（无）") + "\n"
                + "请重新调用 draft 生成本次提交草稿后停在「待确认」。")

    # ---------------- 监控 / 报告（复用真实 Orchestrator 原语） ----------------
    def tool_monitor(self, args: dict) -> str:
        flow = self._load_flow()
        orch = self._ensure_orch()
        hpc = getattr(orch, "hpc", None)
        if hpc is None:
            return ("未连接超算（未配置 SSH 主机/账号），无法查询作业进度；"
                    "作业若已提交会照常在超算运行。")
        try:
            monitor = getattr(orch, "monitor", None)
            if monitor is None:
                return "当前环境不支持监控推进（Orchestrator 缺少 monitor）"
            return monitor(self.store, self.project_id, self.task_id, flow)
        except Exception as exc:  # noqa: BLE001
            return f"监控查询失败：{type(exc).__name__}（{exc}）"

    def tool_stop_monitor(self, args: dict) -> str:
        """M56：用户终止当前计算——非终态作业置 canceled、phase=done，
        后台监控与自动补提随即停止。"""
        flow = self._load_flow()
        orch = self._ensure_orch()
        stop = getattr(orch, "stop_monitor", None)
        if stop is None:
            return "当前环境不支持终止监控（Orchestrator 缺少 stop_monitor）"
        try:
            return stop(self.store, self.project_id, self.task_id,
                        dict(flow or {}))
        except Exception as exc:  # noqa: BLE001
            return f"终止失败：{type(exc).__name__}（{exc}）"

    def tool_report(self, args: dict) -> str:
        flow = self._load_flow()
        jobs = (flow.get("plan") or {}).get("jobs") or []
        terminal = {"completed", "failed", "not_converged", "canceled", "not_found", "skipped"}
        if not jobs or not all(j.get("status") in terminal for j in jobs):
            return ("作业尚未全部终态（仍有作业在模拟/等待，或尚未提交/无结果），"
                    "暂无法生成最终报告。可先调用 monitor 推进状态。")
        orch = self._ensure_orch()
        finalize = getattr(orch, "finalize_report", None)
        if finalize is None:
            return "当前环境不支持生成报告（Orchestrator 缺少 finalize_report）"
        try:
            report = finalize(self.store, self.project_id, self.task_id, flow)
        except Exception as exc:  # noqa: BLE001
            return f"报告生成失败：{type(exc).__name__}（{exc}）"
        flow["phase"] = "done"
        flow["report"] = report
        self._save_flow(flow)
        return f"作业已全部终态，报告如下：\n\n{report}"
