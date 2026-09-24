# -*- coding: utf-8 -*-
"""agent 工具集（M31）：把真实操作暴露给 LLM 决策调用，全程过安全门。

设计原则（对齐安全边界 + 产品红线）：
- LLM 工具名先经过显式 allowlist；未知工具与自由命令入口稳定拒绝。
- 每个写操作都绑定一个可持久化、单次使用的确认 action，并在执行前重新校验。
- 写操作只允许落在本任务计算目录（local_dir）内；读取只限本地工作区/计算目录。
- ``submit`` 只把流程停在「待确认」，绝不代替用户执行 sbatch —— 真实提交只由
  系统在用户批准并原子 claim 一张精确绑定的单次确认卡后执行。
- 所有工具只返回文本回执给 LLM，不返回密钥/口令，不做网络外带。
"""
from __future__ import annotations

import json
import copy
import hashlib
import logging
import os
import posixpath
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from backend.app.generators.kpoints import KpointsGenerator

from .contracts import ToolFailure
from .consent import (PendingConsentError, card_payload, claim_action,
                       finish_action, get_card, save_card, task_lock)
from .config import AiModeConfig, execution_mode, load_settings
from .exec.errors import ExecutionPolicyViolation
from .exec.policy import check_path_in_bounds
from .incar_draft import (IncarUnknownTagError, build_incar_action,
                           commit_incar_action)
from .input_checks import bounded_fingerprint, read_bound_input, validate_input_set
from backend.input_validation import InputValidationError
from .projects import ProjectStore
from .schemas import PlanSnapshot, PlanStep
from .tools.draft import (find_remote_submit_script,
                           fingerprint_local_submit_script,
                           fingerprint_remote_submit_script,
                           precheck_snapshot,
                           resolve_user_submit_script, submit_command)
from .workflow.plan import validate_plan
from .workspace import snapshot_hpc_workspace, snapshot_workspace

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

_WS_READ_CAP = 12000             # ws_read 单文件预览上限
_HPC_READ_CAP = 12000            # hpc_read 单文件预览上限
_HPC_UPLOAD_CAP = 64 * 1024 * 1024   # hpc_upload 单文件大小上限（64 MB）

_SAFE_TEXT_NAMES = frozenset({
    "INCAR", "POSCAR", "CONTCAR", "KPOINTS", "OUTCAR", "OSZICAR",
    "IBZKPT", "EIGENVAL", "DOSCAR", "PROCAR", "XDATCAR", "VASPRUN.XML",
})
_SAFE_TEXT_SUFFIXES = frozenset({".txt", ".log", ".out"})


def _read_policy_error(relative_path: str) -> str:
    """Return a stable denial code for sensitive or unknown file classes."""
    name = posixpath.basename(relative_path.replace("\\", "/")).upper()
    low = name.lower()
    if (re.fullmatch(r"POTCAR(?:[._-].*)?", name)
            or re.fullmatch(r"(?:WAVE|CHG)CAR(?:[._-].*)?", name)):
        return ToolFailure('AI_SENSITIVE_FILE_DENIED', "[AI_SENSITIVE_FILE_DENIED] 该 VASP 用户/大型产物禁止读取或展示")
    if (low.startswith(".env") or low in {"config.json", "credentials", "credentials.json"}
            or low.startswith(("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"))
            or any(token in low for token in ("private_key", "secret", "credential"))
            or Path(low).suffix in {".pem", ".key", ".p12", ".pfx"}):
        return ToolFailure('AI_SENSITIVE_FILE_DENIED', "[AI_SENSITIVE_FILE_DENIED] 凭据、私钥或密钥配置禁止读取")
    if name not in _SAFE_TEXT_NAMES and Path(low).suffix not in _SAFE_TEXT_SUFFIXES:
        return ToolFailure('AI_UNKNOWN_TEXT_DENIED', "[AI_UNKNOWN_TEXT_DENIED] 未知文件类型不允许进入 LLM 上下文")
    return ""


def _decode_safe_text(data: bytes, *, cap: int) -> tuple[str, str]:
    if len(data) > cap:
        return "", f"[AI_FILE_TOO_LARGE] 文件超过安全读取上限 {cap} B"
    if b"\x00" in data:
        return "", "[AI_BINARY_FILE_DENIED] 二进制文件禁止读取"
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "", "[AI_BINARY_FILE_DENIED] 文件不是受支持的 UTF-8 文本"
    if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in text):
        return "", "[AI_BINARY_FILE_DENIED] 文件包含二进制控制字符"
    return text, ""


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
_TERMINAL_SKIP = ("completed", "failed", "not_converged", "canceled",
                  "skipped", "blocked", "unknown")


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

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _ensure_artifacts(self, flow: dict | None = None) -> dict[str, dict]:
        """Register only exact VASP inputs from the user-selected workspace."""
        flow = dict(flow or self._load_flow())
        root_raw = str(self._task().get("local_workspace") or "").strip()
        artifacts: dict[str, dict] = {}
        if root_raw:
            root = Path(root_raw).expanduser().resolve()
            candidates = [root / name for name in
                          ("INCAR", "POSCAR", "KPOINTS", "POTCAR")]
            jobs = ((flow.get("plan") or {}).get("jobs")) or []
            for job in jobs:
                job_dir = root / str(job.get("key") or "")
                candidates.extend(job_dir / name for name in
                                  ("INCAR", "POSCAR", "KPOINTS", "POTCAR"))
                try:
                    candidates.extend(p for p in job_dir.iterdir()
                                      if p.is_file() and p.suffix.lower() == ".sh")
                except OSError:
                    pass
            try:
                candidates.extend(p for p in root.iterdir()
                                  if p.is_file() and p.suffix.lower() == ".sh")
            except OSError:
                pass
            for path in sorted(set(candidates)):
                try:
                    resolved = path.resolve()
                    resolved.relative_to(root)
                    if not resolved.is_file():
                        continue
                    size = resolved.stat().st_size
                    digest = self._sha256_file(resolved)
                except (OSError, ValueError):
                    continue
                relative = str(path.relative_to(root)).replace("\\", "/")
                name = path.name
                artifact_id = "art_" + hashlib.sha256(
                    f"{self.project_id}\0{self.task_id}\0{relative}\0{digest}".encode()
                ).hexdigest()[:20]
                artifacts[artifact_id] = {
                    "artifact_id": artifact_id, "path": relative,
                    "name": name, "size": size, "sha256": digest,
                    "provenance": "user_selected_workspace",
                    "content_access": ("denied" if name == "POTCAR" or path.suffix.lower() == ".sh"
                                       else "text_safe"),
                }
        if flow.get("artifacts") != artifacts:
            flow["artifacts"] = artifacts
            self._save_flow(flow)
        return artifacts

    def _job_target_dir(self, key: str) -> Optional[Path]:
        """作业子目录：<key> 目录已存在时返回它，否则返回 None（表示用计算目录根）。"""
        key = str(key or "").strip()
        if not key:
            return None
        root = self.local_dir().resolve()
        cand = (root / key).resolve()
        try:
            cand.relative_to(root)
        except ValueError:
            return None
        return cand if cand.is_dir() else None

    @staticmethod
    def _clean_job_subdir(raw) -> Optional[str]:
        """copy_inputs/hpc_upload 等受限工具的作业目录参数：

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
        from .computation import normalize
        normalize(flow)
        flow["updated_at"] = _now_iso()
        self.store.update_task(self.project_id, self.task_id,
                               flow=flow,
                               status=_PHASE_STATUS.get(flow.get("phase"),
                                                        "planned"))

    def _ensure_orch(self):
        if self._orch is None and self._orch_factory is not None:
            self._orch = self._orch_factory()
        if self._orch is None:
            from .orchestrator import Orchestrator
            self._orch = Orchestrator.from_settings(self.cfg)
        return self._orch

    def _execution_mode(self) -> str:
        orch = self._ensure_orch()
        hpc = getattr(orch, "hpc", None)
        declared = getattr(orch, "execution_mode",
                           getattr(hpc, "execution_mode", None))
        return execution_mode(hpc, explicit=declared)

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
    _DISABLED_LLM_TOOLS = frozenset({
        "run_exec", "hpc_exec", "hpc_write_script", "write_input",
    })
    _LLM_TOOL_METHODS = {
        "get_state": "tool_get_state",
        "ws_list": "tool_ws_list",
        "ws_read": "tool_ws_read",
        "mp_search": "tool_mp_search",
        "mp_import_poscar": "tool_mp_import_poscar",
        "hpc_list": "tool_hpc_list",
        "hpc_read": "tool_hpc_read",
        "hpc_upload": "tool_hpc_upload",
        "stop_monitor": "tool_stop_monitor",
        "plan": "tool_plan",
        "copy_inputs": "tool_copy_inputs",
        "propose_incar": "tool_propose_incar",
        "generate_kpoints": "tool_generate_kpoints",
        "precheck": "tool_precheck",
        "draft": "tool_draft",
        "submit": "tool_submit",
        "select_jobs": "tool_select_jobs",
        "diagnose_job": "tool_diagnose_job",
        "retry_job": "tool_retry_job",
    }

    def handle(self, name: str, args: dict) -> str:
        """Serialize each full tool operation with consent and monitoring."""
        with task_lock(self.project_id, self.task_id):
            return self._handle_locked(name, args)

    def _handle_locked(self, name: str, args: dict) -> str:
        """执行一个工具并返回给 LLM 的回执文本；任何异常都不会中断决策循环。"""
        normalized = str(name).strip().lower()
        if normalized in self._DISABLED_LLM_TOOLS:
            code = ("AI_FREEFORM_EXEC_DISABLED" if normalized in
                    {"run_exec", "hpc_exec"} else "AI_TOOL_NOT_ALLOWED")
            return f"[{code}] 工具 {normalized} 已由安全策略永久禁用，未执行任何操作"
        method_name = self._LLM_TOOL_METHODS.get(normalized)
        if method_name is None:
            return ToolFailure('AI_TOOL_NOT_ALLOWED', f"[AI_TOOL_NOT_ALLOWED] 未允许的工具：{name}；未执行任何操作")
        func = getattr(self, method_name)
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
        artifacts = self._ensure_artifacts(flow)
        flow = self._load_flow()
        plan = flow.get("plan") or {}
        jobs = plan.get("jobs") or []
        job_lines = "\n".join(
            f"- {j.get('key')}（{j.get('label') or ''}，{j.get('kind') or 'vasp'}）"
            f" status={j.get('status') or 'draft'} attempt_id={j.get('attempt_id') or 'legacy'}"
            + f" precheck={'ok' if (j.get('precheck') or {}).get('ok') else 'blocked_or_unchecked'} draft={'ready' if j.get('draft') else 'none'}"
            + (f" slurm_id={j.get('slurm_id')}" if j.get("slurm_id") else "")
            for j in jobs) or "（暂无规划）"
        drafts = flow.get("draft") or []
        draft_names = "、".join(
            (f"{d.get('job_key')}:{d.get('script_name')}"
             + (f"@{d.get('dir')}" if d.get("dir") else ""))
            for d in drafts) or "（暂无）"
        pre = flow.get("precheck") or {}
        issue_n = len(pre.get("issues") or [])
        pre_text = "按各计算查看" if len(jobs) > 1 else ("ok" if pre.get("ok") else (f"{issue_n} 项问题" if issue_n else "未检查"))
        artifact_text = "、".join(
            f"{item['name']}={artifact_id}({item['size']} B)"
            for artifact_id, item in artifacts.items()
        ) or "（无）"
        return (
            "【当前计算流程状态】\n"
            f"- 阶段 phase：{flow.get('phase') or '（未开始）'}\n"
            f"- 目标 goal：{flow.get('goal') or self._task().get('goal') or '（未填写）'}\n"
            f"- 规划 strategy：{plan.get('strategy') or '（未规划）'}\n{job_lines}\n"
            f"- 本地计算目录：{flow.get('local_dir') or self.local_dir()}\n"
            f"- 超算目录 hpc_dir：{flow.get('hpc_dir') or '（未设置）'}\n"
            f"- 提交前检查：{pre_text}\n"
            f"- 提交草稿：{draft_names}\n"
            f"- 已上传超算：{'是' if flow.get('uploaded') else '否'}\n"
            f"- 用户登记输入 artifact_id：{artifact_text}"
        )

    def tool_ws_list(self, args: dict) -> str:
        root = self._task().get("local_workspace") or ""
        _found, text = snapshot_workspace(
            root, max_preview_bytes=0, preview_total_cap=0)
        return text

    def tool_ws_read(self, args: dict) -> str:
        rel = str(args.get("path") or "").strip()
        if not rel:
            return ToolFailure('INVALID_TOOL_ARGUMENT', "缺少参数 path（本地工作区内的相对路径）")
        policy_error = _read_policy_error(rel)
        if policy_error:
            return ToolFailure('TOOL_POLICY_OR_PRECONDITION', policy_error)
        root = self._task().get("local_workspace") or ""
        if not root:
            return ToolFailure('TOOL_PRECONDITION_FAILED', "任务未设置本地工作区（local_workspace）")
        rootp = Path(root).expanduser().resolve()
        try:
            target = check_path_in_bounds(rel, rootp, write=False)
        except ExecutionPolicyViolation as exc:
            return ToolFailure('TOOL_OPERATION_FAILED', f"安全策略拒绝：{getattr(exc, 'reason', str(exc))}")
        if not target.is_file():
            return ToolFailure('TOOL_OPERATION_FAILED', f"文件不存在：{rel}")
        try:
            if target.stat().st_size > _WS_READ_CAP:
                return ToolFailure('AI_FILE_TOO_LARGE', f"[AI_FILE_TOO_LARGE] 文件超过安全读取上限 {_WS_READ_CAP} B")
            data = target.read_bytes()
        except OSError as exc:
            return ToolFailure('TOOL_OPERATION_FAILED', f"读取失败：{type(exc).__name__}")
        text, decode_error = _decode_safe_text(data, cap=_WS_READ_CAP)
        if decode_error:
            return ToolFailure('TOOL_POLICY_OR_PRECONDITION', decode_error)
        return f"--- 文件 {rel} ---\n{text}"

    def tool_precheck(self, args: dict) -> str:
        required = ["INCAR", "POSCAR", "KPOINTS", "POTCAR"]
        local_dir = self.local_dir()
        rows: list[str] = []
        issues: list[dict] = []
        input_records: list[dict] = []
        script_records: list[dict] = []
        ok = True
        flow = self._load_flow()
        mode = self._execution_mode()
        flow["execution_mode"] = mode
        jobs = (flow.get("plan") or {}).get("jobs") or []
        if not jobs:
            return ToolFailure('TOOL_PRECONDITION_FAILED', "尚未规划作业：请先调用 plan 再 precheck")
        from .computation import select_job
        selected = select_job(flow, args)
        jobs = [selected]
        remote = (self._hpc_root(flow) or "").rstrip("/")
        hpc = None
        try:
            hpc = getattr(self._ensure_orch(), "hpc", None)
        except Exception:  # noqa: BLE001
            hpc = None
        for job in jobs:
            if job.get("status") in _TERMINAL_SKIP:
                continue
            key = job["key"]
            job_dir = self._job_target_dir(key)
            base = job_dir if job_dir is not None else local_dir
            suffix = (f"（作业目录 {key}）" if job_dir is not None else "")
            calc = (self._remote_job_dir(hpc, remote, key)
                    if (hpc is not None and remote) else "")
            contents: dict[str, bytes] = {}
            for name in required:
                name = str(name or "").strip()
                if not name:
                    continue
                # With a remote target, every required file must already be
                # present remotely via an explicitly confirmed upload action.
                try:
                    if calc:
                        path = f"{calc}/{name}"
                        fingerprint = bounded_fingerprint(name, path, hpc=hpc)
                        source = "remote"
                        where = "（超算）"
                    else:
                        target = (base / name).resolve()
                        target.relative_to(local_dir.resolve())
                        fingerprint = bounded_fingerprint(name, target)
                        source = "local"
                        where = "（本地）"
                    contents[name] = read_bound_input(name, fingerprint,
                                                      hpc=hpc if calc else None)
                    input_records.append({"job_key": key, "name": name,
                                          "source": source, **fingerprint})
                    rows.append(f"- [ok] {name} 非空且哈希已绑定{where}{suffix}")
                except InputValidationError as exc:
                    ok = False
                    rows.append(f"- [error] {name} {exc}{suffix}，硬预检阻止提交")
                    issues.append({"job": key, "file": name, "level": "error",
                                   "message": f"{name} {exc}{suffix}"})
                except Exception:  # noqa: BLE001
                    ok = False
                    rows.append(f"- [error] {name} 缺失、为空或无法哈希，硬预检阻止提交" + suffix)
                    issues.append({"job": key, "file": name, "level": "error",
                                   "message": f"{name} 缺失、为空或无法哈希{suffix}"})
            if len(contents) == len(required):
                try:
                    validate_input_set(contents)
                    rows.append(f"- [ok] 基础格式与物种顺序检查通过{suffix}；参数与科学适用性仍需用户判断")
                except InputValidationError as exc:
                    ok = False
                    rows.append(f"- [error] 基础输入检查失败：{exc}{suffix}")
                    issues.append({"job": key, "file": "基础输入", "level": "error",
                                   "message": str(exc)})
            # 提交脚本：超算作业目录优先，本地回退（M51 超算为主）
            script_ok = False
            actual_script: dict | None = None
            if calc:
                try:
                    rname = find_remote_submit_script(hpc, calc)
                    if rname:
                        script_ok = True
                        actual_script = {
                            "source": "remote", "directory": calc,
                            "script_name": rname,
                            **fingerprint_remote_submit_script(hpc, calc, rname),
                        }
                        rows.append(f"- [ok] 提交脚本 {rname} 存在（超算）"
                                    f"{suffix}（用户提供）")
                except RuntimeError as exc:
                    rows.append(f"- [error] {exc}{suffix}")
                    ok = False
                    issues.append({"job": key, "file": "提交脚本(*.sh)",
                                   "level": "error", "message": str(exc)})
                    script_ok = True  # 已报错，不再重复报本地缺失
            if not script_ok and calc:
                ok = False
                rows.append(f"- [error] 超算作业目录缺少唯一用户提交脚本{suffix}")
                issues.append({"job": key, "file": "提交脚本(*.sh)",
                               "level": "error",
                               "message": "超算作业目录缺少唯一用户提交脚本"})
            if not script_ok and not calc:
                try:
                    local_script = resolve_user_submit_script(base)
                    actual_script = {
                        "source": "local", "directory": str(base),
                        "script_name": local_script.name,
                        **self._script_fingerprint(
                            source="local", directory=str(base),
                            script_name=local_script.name),
                    }
                    rows.append(f"- [ok] 提交脚本(*.sh) 存在（本地）{suffix}"
                                "（用户提供）")
                except (RuntimeError, ValueError) as exc:
                    ok = False
                    rows.append(f"- [error] {exc}；脚本必须由用户提供{suffix}")
                    issues.append({"job": key, "file": "提交脚本(*.sh)",
                                   "level": "error", "message": str(exc)})
            attestation = (flow.get("script_attestations") or {}).get(key)
            attested = (isinstance(attestation, dict)
                        and attestation.get("attempt_id") == job.get("attempt_id")
                        and isinstance(actual_script, dict)
                        and all(attestation.get(field) == actual_script.get(field)
                                for field in ("source", "directory", "script_name",
                                              "normalized_path", "sha256", "size")))
            if not attested:
                ok = False
                rows.append(f"- [error] 提交脚本尚未由用户认领并绑定 SHA-256{suffix}")
                issues.append({"job": key, "file": "提交脚本认领", "level": "error",
                               "message": "缺少有效脚本认领"})
            else:
                script_records.append({"job_key": key, **actual_script})
        from .scheduler_profile import target_binding
        snapshot, digest = precheck_snapshot(
            execution_mode=mode, inputs=input_records, scripts=script_records,
            scheduler_target=target_binding(self.cfg))
        from .computation import bind_precheck
        bind_precheck(flow, selected, {"ok": ok, "issues": issues, "hard": True,
                            "execution_mode": mode, "snapshot": snapshot,
                            "digest": digest})
        self._save_flow(flow)
        prefix = "提交前硬检查通过：" if ok else "提交前硬检查失败（禁止提交）："
        return prefix + "\n" + "\n".join(rows)

    def execute_action(self, action_id: str) -> str:
        """Serialize one complete consent action with all task flow mutations."""
        with task_lock(self.project_id, self.task_id):
            return self._execute_action_locked(action_id)

    def _execute_action_locked(self, action_id: str) -> str:
        """Claim and execute one exact approved action without replaying LLM args."""
        action = claim_action(self.store, self.project_id, self.task_id,
                              action_id)
        if action is None:
            current = get_card(self.store, self.project_id, self.task_id,
                               action_id) or {}
            return (current.get("result")
                    or f"操作不可执行（state={current.get('state') or 'missing'}）")
        binding = action.get("binding") or {}
        operation = binding.get("operation")
        try:
            # MP import is a local-only action and must not require an HPC adapter.
            if operation != "mp_poscar_write" and binding.get("execution_mode") != self._execution_mode():
                raise ValueError("HPC execution mode changed after confirmation")
            if operation == "incar_write":
                result = commit_incar_action(action, root=self.local_dir())
            elif operation == "copy_inputs":
                result = self._execute_copy_action(binding)
            elif operation == "hpc_upload":
                files = getattr(self.store, 'file_actions', None)
                if files:
                    current_root = self._hpc_root()
                    if current_root != binding.get("remote_root"):
                        raise ValueError("remote workspace changed after confirmation")
                    with files.legacy_upload(self.project_id, self.task_id,
                                             binding, cfg=self.cfg) as (before_write, hpc, verify_after):
                        result = self._execute_upload_action(
                            binding, ready=(hpc, current_root, ""),
                            before_write=before_write, verify_after=verify_after)
                else:
                    result = self._execute_upload_action(binding)
            elif operation == "kpoints_write":
                result = self._execute_deterministic_text_action(binding)
            elif operation == "mp_poscar_write":
                if not str(self._task().get("local_workspace") or "").strip():
                    raise ValueError("local workspace is no longer selected")
                self._mp_poscar_target(binding["relative_path"])
                result = self._execute_deterministic_text_action(binding)
                flow = self._load_flow()
                imported = dict(flow.get("material_imports") or {})
                imported[binding["relative_path"]] = {
                    "material_id": binding["material_id"],
                    "source_url": binding["source_url"],
                    "sha256": binding["proposal_sha256"],
                }
                flow["material_imports"] = imported
                self._save_flow(flow)
                result += f"，来源 Materials Project {binding['material_id']}；未上传或提交。"
            elif operation == "script_attestation":
                result = self._execute_script_attestation(action)
            elif operation == "retry_job":
                result = self._ensure_orch().retry_job(
                    self.store, self.project_id, self.task_id, action)
            else:
                raise ValueError(f"unsupported consent operation: {operation}")
        except Exception as exc:  # noqa: BLE001
            result = f"操作失败且未重试：{type(exc).__name__}（{exc}）"
            saved = get_card(self.store,self.project_id,self.task_id,action_id) or {}
            state = 'unknown' if operation == 'hpc_upload' and saved.get('file_dispatch_at') else 'failed'
            finish_action(self.store, self.project_id, self.task_id,
                          action_id, state=state, result=result)
            return result
        finish_action(self.store, self.project_id, self.task_id,
                      action_id, state="executed", result=result)
        return result

    def _execute_copy_action(self, binding: dict) -> str:
        source_root = Path(self._task().get("local_workspace") or "").resolve()
        destination_root = self.local_dir().resolve()
        if (str(source_root) != binding.get("source_root")
                or str(destination_root) != binding.get("destination_root")):
            raise ValueError("workspace changed after confirmation")
        copied: list[str] = []
        for item in binding.get("copies") or []:
            source = check_path_in_bounds(item["source_relative_path"],
                                          source_root, write=False)
            if (not source.is_file()
                    or source.stat().st_size != item["source_size"]
                    or self._sha256_file(source) != item["source_sha256"]):
                raise ValueError("registered source changed after confirmation")
            destination = check_path_in_bounds(
                item["destination_relative_path"], destination_root, write=True)
            current_hash = self._sha256_file(destination) if destination.is_file() else ""
            if current_hash != item["destination_base_sha256"]:
                raise ValueError("copy destination changed after confirmation")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_name = ""
            try:
                with tempfile.NamedTemporaryFile(
                        mode="wb", dir=destination.parent, prefix=".copy.",
                        suffix=".tmp", delete=False) as output:
                    temp_name = output.name
                    with source.open("rb") as input_file:
                        shutil.copyfileobj(input_file, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temp_name, destination)
            finally:
                if temp_name and os.path.exists(temp_name):
                    os.unlink(temp_name)
            copied.append(item["destination_relative_path"])
        return "已原子复制确认的登记输入：" + "、".join(copied)

    def _execute_deterministic_text_action(self, binding: dict) -> str:
        root = self.local_dir().resolve()
        if str(root) != binding.get("workspace_root"):
            raise ValueError("workspace changed after confirmation")
        target = check_path_in_bounds(binding["relative_path"], root, write=True)
        current_hash = self._sha256_file(target) if target.is_file() else ""
        if current_hash != binding["base_sha256"]:
            raise ValueError("target changed after preview")
        data = str(binding["content"]).encode("utf-8")
        if (len(data) != binding["proposal_size"]
                or hashlib.sha256(data).hexdigest() != binding["proposal_sha256"]):
            raise ValueError("deterministic proposal hash mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent,
                                             prefix=".kpoints.", suffix=".tmp",
                                             delete=False) as handle:
                temp_name = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
        return (f"已原子写入 `{binding['relative_path']}`（SHA-256 "
                f"{binding['proposal_sha256'][:12]}…）")

    def _execute_upload_action(self, binding: dict, *, ready=None, before_write=None,
                               verify_after=None) -> str:
        hpc, root, err = ready if ready is not None else self._hpc_ready()
        if err:
            raise ValueError(err)
        if root != binding.get("remote_root"):
            raise ValueError("remote workspace changed after confirmation")
        if str(self.local_dir().resolve()) != binding.get("local_root"):
            raise ValueError("local workspace changed after confirmation")
        source = check_path_in_bounds(binding["source_relative_path"],
                                      self.local_dir(), write=False)
        if not source.is_file():
            raise ValueError("registered source changed after confirmation")
        data = source.read_bytes()
        if (len(data) != binding["source_size"]
                or hashlib.sha256(data).hexdigest() != binding["source_sha256"]):
            raise ValueError("registered source changed after confirmation")
        rel = str(binding["remote_relative_path"])
        remote_path = f"{root.rstrip('/')}/{rel}"
        parent_rel = posixpath.dirname(rel)
        atomic_write = getattr(hpc, "atomic_write_file", None)
        if atomic_write is None:
            raise ValueError("HPC adapter does not support verified atomic upload")
        # Pure local validation is complete; keep these exact bytes, persist
        # possible dispatch, then enter the first remote mutation once.
        if before_write:
            before_write()
        if parent_rel:
            current = root.rstrip("/")
            for segment in parent_rel.split("/"):
                current += "/" + segment
                try:
                    hpc.mkdir(current)
                except Exception:
                    if getattr(hpc, "stat", lambda _p: None)(current) is None:
                        raise
        written = atomic_write(remote_path, data,
                               expected_sha256=binding["source_sha256"])
        if (written != binding["source_size"]
                or hpc.sha256_file(remote_path) != binding["source_sha256"]):
            raise ValueError("remote upload verification failed")
        if verify_after:
            verify_after()
        flow = self._load_flow()
        uploaded = dict(flow.get("uploaded_artifacts") or {})
        uploaded[binding["artifact_id"]] = {
            "remote_path": remote_path,
            "sha256": binding["source_sha256"],
            "size": binding["source_size"],
        }
        flow["uploaded_artifacts"] = uploaded
        flow["uploaded"] = True
        self._save_flow(flow)
        return (f"已通过 SFTP 上传确认的登记输入到 `{remote_path}` "
                f"（SHA-256 {binding['source_sha256'][:12]}…）")

    def _execute_script_attestation(self, action: dict) -> str:
        binding = action.get("binding") or {}
        from .computation import select_job
        flow = self._load_flow()
        job = select_job(flow, binding)
        verified: dict[str, dict] = {}
        for item in binding.get("scripts") or []:
            if item.get("job_key") != job["key"] or item.get("attempt_id") != job.get("attempt_id"):
                raise ValueError("script attestation identity changed")
            current = self._script_fingerprint(
                source=item["source"], directory=item["directory"],
                script_name=item["script_name"])
            for key in ("normalized_path", "sha256", "size"):
                if current.get(key) != item.get(key):
                    raise ValueError("submit script changed after attestation preview")
            verified[item["job_key"]] = {
                **item, "action_id": action["action_id"],
                "binding_hash": action["binding_hash"],
            }
        flow = self._load_flow()
        flow.setdefault("script_attestations", {}).update(verified)
        self._save_flow(flow)
        return "已认领并绑定当前提交脚本：" + "、".join(sorted(verified))

    def _script_fingerprint(self, *, source: str, directory: str,
                            script_name: str) -> dict:
        if source == "remote":
            hpc = getattr(self._ensure_orch(), "hpc", None)
            if hpc is None:
                raise ValueError("未连接超算，无法校验远端脚本")
            return fingerprint_remote_submit_script(hpc, directory, script_name)
        script = resolve_user_submit_script(Path(directory))
        if script.name != script_name:
            raise ValueError("本地提交脚本候选已变化")
        try:
            script.resolve().relative_to(self.local_dir().resolve())
        except ValueError as exc:
            raise ValueError("本地提交脚本越出用户工作区") from exc
        return fingerprint_local_submit_script(script)

    def tool_run_exec(self, args: dict) -> str:
        del args
        return ToolFailure('AI_FREEFORM_EXEC_DISABLED', "[AI_FREEFORM_EXEC_DISABLED] 自由命令执行在 AI 模式中已禁用；"
                "请使用明确、受约束的工具。")

    def tool_hpc_exec(self, args: dict) -> str:
        del args
        return ToolFailure('AI_FREEFORM_EXEC_DISABLED', "[AI_FREEFORM_EXEC_DISABLED] 自由命令执行在 AI 模式中已禁用；"
                "请使用明确、受约束的工具。")

    # ---------------- 本地 -> 超算受限上传（SFTP，非 scp） ----------------
    def tool_hpc_upload(self, args: dict) -> str:
        """Create one confirmation action for one registered artifact upload."""
        artifact_id = str(args.get("artifact_id") or "").strip()
        if not artifact_id:
            return ToolFailure('AI_ARTIFACT_REQUIRED', "[AI_ARTIFACT_REQUIRED] 上传只接受用户工作区登记的 artifact_id；"
                    "未执行任何远程写入")
        flow = self._load_flow()
        artifact = self._ensure_artifacts(flow).get(artifact_id)
        if not isinstance(artifact, dict):
            return ToolFailure('AI_ARTIFACT_NOT_REGISTERED', "[AI_ARTIFACT_NOT_REGISTERED] artifact_id 未登记或已失效；"
                    "未执行任何远程写入")
        src_rel = str(artifact.get("path") or "").strip().replace("\\", "/")
        if not src_rel:
            return ToolFailure('AI_ARTIFACT_NOT_REGISTERED', "[AI_ARTIFACT_NOT_REGISTERED] 登记项缺少安全相对路径")
        job_key = self._clean_job_subdir(args.get("job_key"))
        if job_key is None:
            return ToolFailure('AI_ARTIFACT_REQUIRED', "[AI_ARTIFACT_REQUIRED] 非法 job_key")
        bad_dir = self._validate_job_dir(job_key)
        if bad_dir:
            return ToolFailure('TOOL_POLICY_OR_PRECONDITION', bad_dir)
        dest_rel = f"{job_key}/{artifact['name']}" if job_key else artifact["name"]
        hpc, root, err = self._hpc_ready()
        if err:
            return ToolFailure('TOOL_POLICY_OR_PRECONDITION', err)
        try:
            target = check_path_in_bounds(src_rel, self.local_dir(),
                                          write=False)
        except ExecutionPolicyViolation as exc:
            return ToolFailure('TOOL_OPERATION_FAILED', f"安全策略拒绝：{getattr(exc, 'reason', str(exc))}")
        if not target.is_file():
            return ToolFailure('TOOL_OPERATION_FAILED', f"本地工作区不存在文件：{src_rel}")
        size = target.stat().st_size
        if size > _HPC_UPLOAD_CAP:
            return (f"文件过大（{size} B > 上限 {_HPC_UPLOAD_CAP} B），"
                    "拒绝上传；请压缩或拆分后再试。")
        binding = {
            "operation": "hpc_upload",
            "project_id": self.project_id, "task_id": self.task_id,
            "job_key": job_key, "execution_kind": "sftp_upload",
            "artifact_id": artifact_id, "source_relative_path": src_rel,
            "source_sha256": artifact["sha256"], "source_size": size,
            "local_root": str(self.local_dir().resolve()),
            "remote_root": root, "remote_relative_path": dest_rel,
            "execution_mode": self._execution_mode(),
        }
        files = getattr(self.store, 'file_actions', None)
        if files:
            identity, session = files.prepare_legacy_upload(
                root, dest_rel, hpc=hpc, cfg=self.cfg)
            binding['file_identity'] = identity
            binding['upload_session'] = session
        registered = False
        try:
            payload = card_payload(
                tool="hpc_upload", args={"artifact_id": artifact_id,
                                          "job_key": job_key},
                risk="medium", reason=("上传确认绑定当前文件哈希、远端目标及当前SSH连接；"
                                       "断线、到期或服务重启后须重新提案。"),
                batch_key=f"upload|{artifact_id}|{root}|{dest_rel}",
                kind="hpc_upload",
                summary=(f"上传已登记输入 `{artifact['name']}`（{size} B，"
                         f"SHA-256 {artifact['sha256']}）到 `{root}/{dest_rel}`"),
                binding=binding,
            )
            if files:
                files.register_legacy_upload(
                    session, payload["action_id"], hpc, payload["expires_at"])
                registered = True
            saved = save_card(self.store, self.project_id, self.task_id,
                              self._load_flow(), payload)
            if files and saved["action_id"] != payload["action_id"]:
                files.release_legacy_upload(payload["binding"])
                if not files.legacy_session_active(saved.get("binding") or {}):
                    return ToolFailure(
                        "UPLOAD_SESSION_EXPIRED",
                        "[UPLOAD_SESSION_EXPIRED] 旧上传卡的SSH会话已失效；请拒绝旧卡后重新提案，未执行远端写入")
        except BaseException:
            if files:
                if registered:
                    files.release_legacy_upload(payload["binding"])
                else:
                    files.discard_legacy_candidate(hpc)
            raise
        raise PendingConsentError(saved)

    # ---------------- 永久禁用的提交脚本写入兼容入口 ----------------
    def tool_hpc_write_script(self, args: dict) -> str:
        """Reject the retired AI-authored script capability."""
        del args
        return ToolFailure('AI_TOOL_NOT_ALLOWED', "[AI_TOOL_NOT_ALLOWED] hpc_write_script 已禁用：提交脚本只能由用户"
                "提供并显式认领；未写入任何远程文件")

    @staticmethod
    def _remote_job_dir(hpc, base: str, key: str) -> str:
        """远端作业目录：远端存在 <base>/<key> 子目录则用之，否则 base
        （对齐 Orchestrator._job_calc_dir 的扁平回退语义）。"""
        stat = getattr(hpc, "stat", None)
        if stat is not None and key:
            try:
                info = stat(f"{base.rstrip('/')}/{key}")
                if info is not None and info.get("is_file") is not True:
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
        if hpc is None or self._execution_mode() == "None":
            return None, "", ("[AI_HPC_BACKEND_UNAVAILABLE] 未连接超算："
                              "未配置 HPC 执行后端，无法访问或修改超算工作区。")
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
            return ToolFailure('TOOL_POLICY_OR_PRECONDITION', err)
        rel = self._hpc_rel(args)
        if rel is None:
            return ToolFailure('INVALID_TOOL_ARGUMENT', "非法 path（仅允许 hpc_dir 内相对路径）")
        target = f"{root.rstrip('/')}/{rel}" if rel else root
        try:
            infos = hpc.list_dir_info(target)
        except Exception as exc:  # noqa: BLE001
            return ToolFailure('TOOL_OPERATION_FAILED', f"列目录失败：{type(exc).__name__}（{target}）")
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
            return ToolFailure('TOOL_POLICY_OR_PRECONDITION', err)
        rel = self._hpc_rel(args)
        if rel is None:
            return ToolFailure('INVALID_TOOL_ARGUMENT', "非法 path（仅允许 hpc_dir 内相对路径）")
        if not rel:
            return ToolFailure('INVALID_TOOL_ARGUMENT', "缺少参数 path（hpc_dir 内的相对路径）")
        policy_error = _read_policy_error(rel)
        if policy_error:
            return ToolFailure('TOOL_POLICY_OR_PRECONDITION', policy_error)
        target = f"{root.rstrip('/')}/{rel}"
        files = getattr(self.store, 'file_actions', None)
        if files and files.has_audit():
            from .file_actions import file_error
            try:
                value=files.inspect(self.project_id,self.task_id,{'path':target,'view':'text'})
                return f"--- 超算文件 {rel} ---\n{value.get('text','')}"
            except Exception as exc:
                error=file_error(exc)
                return ToolFailure(error.code,str(error))
        try:
            stat = getattr(hpc, "stat", lambda _path: None)(target) or {}
            if int(stat.get("size") or 0) > _HPC_READ_CAP:
                return ToolFailure('AI_FILE_TOO_LARGE', f"[AI_FILE_TOO_LARGE] 文件超过安全读取上限 {_HPC_READ_CAP} B")
            data = bytes(hpc.read_file(target, max_bytes=_HPC_READ_CAP + 1))
        except Exception as exc:  # noqa: BLE001
            return ToolFailure('TOOL_OPERATION_FAILED', f"读取失败：{type(exc).__name__}（{target}）")
        text, decode_error = _decode_safe_text(data, cap=_HPC_READ_CAP)
        if decode_error:
            return ToolFailure('TOOL_POLICY_OR_PRECONDITION', decode_error)
        return f"--- 超算文件 {rel} ---\n{text}"

    def hpc_snapshot(self) -> str:
        """超算工作区紧凑快照（给系统提示注入用）；不可用/异常返回空串。"""
        try:
            hpc, root, err = self._hpc_ready()
            if err:
                return ""
            _found, text = snapshot_hpc_workspace(
                hpc, root, max_preview_bytes=0, preview_total_cap=0)
            return text
        except Exception:  # noqa: BLE001
            logger.warning("超算工作区快照生成失败", exc_info=True)
            return ""

    def tool_plan(self, args: dict) -> str:
        strategy = str(args.get("strategy") or "").strip()
        jobs = args.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            return ToolFailure('INVALID_TOOL_ARGUMENT', "plan 需要非空 jobs 数组（[{key,label,kind}]）；请先理解需求再规划")
        goal = str(args.get("goal") or "").strip() or \
            (self._task().get("goal") or "")
        local_dir = self.local_dir()
        flow = self._load_flow()
        if any(j.get("status") in {"submitted", "queued", "running", "unknown", "failed", "not_converged"}
               or j.get("attempt_history") or j.get("submission_state") or j.get("slurm_id")
               for j in flow.get("plan", {}).get("jobs", [])):
            return ToolFailure('AI_RECOVERY_REQUIRED', "[AI_RECOVERY_REQUIRED] 当前计划含在途、未知或失败/恢复作业，不能用重新规划覆盖历史；请先 diagnose_job，明确失败再由用户确认 retry_job。")
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
            return ToolFailure('INVALID_TOOL_ARGUMENT', "plan jobs 中没有有效条目")
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
            return ToolFailure('INVALID_TOOL_ARGUMENT', "规划不合法：" + "；".join(issues)
                    + "（可用作业键：" + "、".join(sorted(keys)) + "）"
                    + "；请修正 requires 后重新 plan")
        flow.update({
            "phase": "running",
            "goal": goal,
            "plan": {"strategy": strategy or "（LLM 自主规划）", "jobs": normalized},
            "local_dir": str(local_dir),
        })
        flow.setdefault("hpc_dir", str(self._task().get("hpc_workspace") or "").strip())
        from .computation import invalidate
        invalidate(flow, None, "计划已变化；请按新计算身份重新确认")
        flow["script_attestations"] = {}
        flow["precheck"] = {"ok": False, "issues": []}
        flow.setdefault("draft", [])
        flow.setdefault("uploaded", False)
        flow["execution_mode"] = self._execution_mode()
        flow.setdefault("waiting", [])
        flow.setdefault("extractions", {})
        flow.setdefault("report", "")
        flow.setdefault("logs", [])
        flow.setdefault("started_at", _now_iso())
        self._save_flow(flow)
        labels = "；".join(f"{j['key']}（{j['label']}，{j['kind']}）" for j in normalized)
        return f"已规划 {len(normalized)} 条作业：{labels}。策略：{strategy or '（未写）'}"

    def tool_write_input(self, args: dict) -> str:
        del args
        return ToolFailure('AI_TOOL_NOT_ALLOWED', "[AI_TOOL_NOT_ALLOWED] 通用 write_input 已禁用；请使用受限 INCAR "
                "草稿或确定性 KPOINTS 流程；未写入任何文件")

    def tool_propose_incar(self, args: dict) -> str:
        """Create a deterministic INCAR preview; never write before approval."""
        job_key = self._clean_job_subdir(args.get("job_key"))
        if job_key is None:
            return ToolFailure('AI_INCAR_INVALID', "[AI_INCAR_INVALID] 非法 job_key")
        bad_dir = self._validate_job_dir(job_key)
        if bad_dir:
            return ToolFailure('AI_INCAR_INVALID', f"[AI_INCAR_INVALID] {bad_dir}")
        rel = f"{job_key}/INCAR" if job_key else "INCAR"
        try:
            binding, diff = build_incar_action(
                root=self.local_dir(), relative_path=rel,
                entries=args.get("entries"), project_id=self.project_id,
                task_id=self.task_id, job_key=job_key,
            )
            binding["execution_mode"] = self._execution_mode()
        except IncarUnknownTagError as exc:
            return ToolFailure('AI_INCAR_UNKNOWN_TAG', f"[AI_INCAR_UNKNOWN_TAG] {exc}")
        except (OSError, UnicodeError, ValueError, OverflowError) as exc:
            return ToolFailure('AI_INCAR_DRAFT_INVALID', f"[AI_INCAR_DRAFT_INVALID] {exc}")
        tags = ", ".join(item["tag"] for item in binding["entries"])
        payload = card_payload(
            tool="propose_incar",
            args={"job_key": job_key, "entries": binding["entries"]},
            risk="medium",
            reason="确认仅对当前 INCAR 内容、目标路径和基础文件哈希生效。",
            batch_key=f"incar|{binding['relative_path']}|{binding['proposal_sha256']}",
            kind="incar_write",
            summary=(f"写入 `{binding['relative_path']}`；参数：{tags}\n"
                     f"SHA-256：{binding['proposal_sha256']}\n\n{diff}"),
            binding=binding,
        )
        saved = save_card(self.store, self.project_id, self.task_id,
                          self._load_flow(), payload)
        raise PendingConsentError(saved)

    def tool_generate_kpoints(self, args: dict) -> str:
        job_key = self._clean_job_subdir(args.get("job_key"))
        if job_key is None:
            return ToolFailure('AI_KPOINTS_INVALID', "[AI_KPOINTS_INVALID] 非法 job_key")
        bad_dir = self._validate_job_dir(job_key)
        if bad_dir:
            return ToolFailure('AI_KPOINTS_INVALID', f"[AI_KPOINTS_INVALID] {bad_dir}")
        grid = args.get("grid")
        centering = str(args.get("centering") or "Gamma")
        try:
            text = KpointsGenerator().uniform(grid, centering,
                                               comment="Generated by VASP-Doctor")
        except Exception as exc:  # noqa: BLE001
            return ToolFailure('AI_KPOINTS_INVALID', f"[AI_KPOINTS_INVALID] {exc}")
        relative = f"{job_key}/KPOINTS" if job_key else "KPOINTS"
        target = check_path_in_bounds(relative, self.local_dir(), write=True)
        base_hash = self._sha256_file(target) if target.is_file() else ""
        data = text.encode("utf-8")
        binding = {
            "operation": "kpoints_write", "project_id": self.project_id,
            "task_id": self.task_id, "job_key": job_key,
            "execution_kind": "deterministic_kpoints_generator",
            "workspace_root": str(self.local_dir().resolve()),
            "relative_path": relative, "base_sha256": base_hash,
            "proposal_sha256": hashlib.sha256(data).hexdigest(),
            "proposal_size": len(data), "content": text,
            "grid": [int(v) for v in grid], "centering": centering,
            "execution_mode": self._execution_mode(),
        }
        payload = card_payload(
            tool="generate_kpoints", args={"job_key": job_key,
                                            "grid": binding["grid"],
                                            "centering": centering},
            risk="medium", reason="确认绑定确定性生成参数、目标路径和内容哈希。",
            batch_key=f"kpoints|{relative}|{binding['proposal_sha256']}",
            kind="kpoints_write",
            summary=f"写入 `{relative}`：\n```\n{text}```",
            binding=binding,
        )
        saved = save_card(self.store, self.project_id, self.task_id,
                          self._load_flow(), payload)
        raise PendingConsentError(saved)

    def tool_mp_search(self, args: dict) -> str:
        from .materials import MaterialsError, search
        if set(args) - {"formula", "limit"}:
            return ToolFailure('MP_INVALID_QUERY', "[MP_INVALID_QUERY] 仅接受 formula 与 limit，不接受 URL、命令或密钥")
        try:
            rows = search(self.cfg.mp_api_key, args.get("formula"), args.get("limit", 5))
        except MaterialsError as exc:
            return ToolFailure('TOOL_OPERATION_FAILED', str(exc))
        return json.dumps({"source": "Materials Project", "materials": rows,
                           "next": "请用户确认材料 ID/晶相，再调用 mp_import_poscar；尚未写入文件"},
                          ensure_ascii=False)

    def _mp_poscar_target(self, relative: str) -> Path:
        # Fixed filename only; reject links even when they resolve within the workspace.
        if Path(relative).name != "POSCAR":
            raise ValueError("MP import only writes POSCAR")
        root = self.local_dir().resolve()
        current = root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
                raise ValueError("MP target cannot contain symbolic links or junctions")
        target = check_path_in_bounds(relative, root, write=True)
        from .materials import MAX_POSCAR_BYTES
        if target.exists() and (not target.is_file() or target.stat().st_size > MAX_POSCAR_BYTES):
            raise ValueError("existing POSCAR is not a bounded regular file")
        return target

    def tool_mp_import_poscar(self, args: dict) -> str:
        from .materials import MaterialsError, fetch_poscar
        if set(args) - {"material_id", "job_key"}:
            return ToolFailure('MP_INVALID_QUERY', "[MP_INVALID_QUERY] 仅接受 material_id 与 job_key，不接受 URL、正文、命令或密钥")
        if not str(self._task().get("local_workspace") or "").strip():
            return ToolFailure('MP_WORKSPACE_REQUIRED', "[MP_WORKSPACE_REQUIRED] 请先为任务选择本地工作区")
        job_key = self._clean_job_subdir(args.get("job_key"))
        jobs = (self._load_flow().get("plan") or {}).get("jobs") or []
        if job_key is None or (job_key and job_key not in {job.get("key") for job in jobs}):
            return ToolFailure('MP_INVALID_TARGET', "[MP_INVALID_TARGET] 目标仅限工作区根目录或已规划作业目录")
        relative = f"{job_key}/POSCAR" if job_key else "POSCAR"
        target = self._mp_poscar_target(relative)
        base_hash = self._sha256_file(target) if target.is_file() else ""
        try:
            proposal = fetch_poscar(self.cfg.mp_api_key, args.get("material_id"))
        except MaterialsError as exc:
            return ToolFailure('TOOL_OPERATION_FAILED', str(exc))
        data = proposal["content"].encode("utf-8")
        binding = {
            "operation": "mp_poscar_write", "project_id": self.project_id,
            "task_id": self.task_id, "job_key": job_key,
            "execution_kind": "local_materials_project_import", "execution_mode": "None",
            "workspace_root": str(self.local_dir().resolve()), "relative_path": relative,
            "base_sha256": base_hash, "proposal_sha256": hashlib.sha256(data).hexdigest(),
            "proposal_size": len(data), **proposal,
        }
        verb = "覆盖现有文件" if target.exists() else "新建文件"
        payload = card_payload(
            tool="mp_import_poscar", args={"material_id": proposal["material_id"], "job_key": job_key},
            risk="medium", reason="仅本地写入，绑定所选材料、目标路径、原文件和新内容哈希；不会上传或提交。",
            batch_key=f"mp|{binding['workspace_root']}|{relative}|{base_hash}|{binding['proposal_sha256']}",
            kind="mp_poscar_write",
            summary=(f"Materials Project {proposal['material_id']} / {proposal['formula']} / "
                     f"{proposal['atom_count']} 原子\n{verb}：`{relative}`\n"
                     f"SHA-256：{binding['proposal_sha256']}\n```\n{proposal['content']}```"),
            binding=binding)
        saved = save_card(self.store, self.project_id, self.task_id, self._load_flow(), payload)
        raise PendingConsentError(saved)

    def tool_copy_inputs(self, args: dict) -> str:
        artifact_ids = args.get("artifact_ids")
        if not isinstance(artifact_ids, list) or not artifact_ids:
            return ToolFailure('AI_ARTIFACT_REQUIRED', "[AI_ARTIFACT_REQUIRED] 需要非空 artifact_ids 数组")
        source = self._task().get("local_workspace") or ""
        if not source:
            return ToolFailure('TOOL_PRECONDITION_FAILED', "任务未设置本地工作区，无法复制输入文件")
        src_root = Path(source).expanduser().resolve()
        local_dir = self.local_dir()
        sub = self._clean_job_subdir(args.get("job_key"))
        if sub is None:
            return ToolFailure('TOOL_PRECONDITION_FAILED', "非法 job_key（仅允许规划内的相对路径）")
        bad_dir = self._validate_job_dir(sub)
        if bad_dir:
            return ToolFailure('TOOL_POLICY_OR_PRECONDITION', bad_dir)
        target_dir = (local_dir / sub) if sub else local_dir
        artifacts = self._ensure_artifacts(self._load_flow())
        copies: list[dict] = []
        for raw_id in artifact_ids:
            artifact_id = str(raw_id or "")
            artifact = artifacts.get(artifact_id)
            if not artifact:
                return ToolFailure('AI_ARTIFACT_NOT_REGISTERED', f"[AI_ARTIFACT_NOT_REGISTERED] 未登记 artifact_id: {artifact_id}")
            src = check_path_in_bounds(artifact["path"], src_root, write=False)
            if (not src.is_file() or src.stat().st_size != artifact["size"]
                    or self._sha256_file(src) != artifact["sha256"]):
                return ToolFailure('AI_ARTIFACT_CHANGED', f"[AI_ARTIFACT_CHANGED] {artifact['name']} 登记后已变化，请重新查看状态")
            dest = (target_dir / artifact["name"]).resolve()
            try:
                dest.relative_to(local_dir.resolve())
            except ValueError:
                return ToolFailure('AI_ARTIFACT_REQUIRED', "[AI_ARTIFACT_REQUIRED] 目标越出计算工作区")
            if src.resolve() == dest:
                continue
            dest_hash = self._sha256_file(dest) if dest.is_file() else ""
            copies.append({
                "artifact_id": artifact_id,
                "source_relative_path": artifact["path"],
                "source_sha256": artifact["sha256"],
                "source_size": artifact["size"],
                "destination_relative_path": str(dest.relative_to(local_dir.resolve())).replace("\\", "/"),
                "destination_base_sha256": dest_hash,
            })
        if not copies:
            return "已登记输入本来就在目标目录；未执行复制，也无需确认"
        binding = {
            "operation": "copy_inputs", "project_id": self.project_id,
            "task_id": self.task_id, "job_key": sub,
            "execution_kind": "atomic_local_copy",
            "source_root": str(src_root),
            "destination_root": str(local_dir.resolve()), "copies": copies,
            "execution_mode": self._execution_mode(),
        }
        payload = card_payload(
            tool="copy_inputs", args={"artifact_ids": list(artifact_ids),
                                       "job_key": sub},
            risk="medium", reason="复制确认仅绑定登记源文件哈希和目标路径。",
            batch_key="copy|" + hashlib.sha256(
                json.dumps(binding, sort_keys=True).encode()).hexdigest(),
            kind="copy_inputs",
            summary="复制已登记输入到计算目录：\n" + "\n".join(
                f"- {item['source_relative_path']} → {item['destination_relative_path']}"
                for item in copies),
            binding=binding,
        )
        saved = save_card(self.store, self.project_id, self.task_id,
                          self._load_flow(), payload)
        raise PendingConsentError(saved)

    # ---------------- 提交边界（不代替用户真实提交） ----------------
    def tool_draft(self, args: dict) -> str:
        flow = self._load_flow()
        jobs = (flow.get("plan") or {}).get("jobs") or []
        if not jobs:
            return ToolFailure('TOOL_PRECONDITION_FAILED', "尚未规划作业：请先调用 plan 再 draft（参考 get_state）")
        remote = (self._hpc_root(flow) or "").rstrip("/")
        local_dir = self.local_dir()
        hpc = None
        try:
            hpc = getattr(self._ensure_orch(), "hpc", None)
        except Exception:  # noqa: BLE001
            hpc = None
        from .computation import select_job
        selected = select_job(flow, args)
        active = [selected]
        if not active:
            return ("全部作业均已被跳过或缺终态，无需生成草稿；"
                    "可用 select_jobs 重新选择后再 draft。")
        missing: list[str] = []
        resolved: list[tuple] = []
        for job in active:
            job_local = self._job_target_dir(job["key"]) or local_dir
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
            try:
                fingerprint = self._script_fingerprint(
                    source=source,
                    directory=(calc_dir if source == "remote" else str(job_local)),
                    script_name=script_name,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                missing.append(f"- {job['key']}：脚本校验失败（{exc}）")
                continue
            resolved.append((job, job_local, calc_dir, script_name, source,
                             fingerprint))
        if missing:
            flow["phase"] = "blocked"
            self._save_flow(flow)
            return ToolFailure('TOOL_PRECONDITION_FAILED', "无法生成提交草稿：缺少提交脚本（*.sh）。"
                    "脚本必须由用户放在超算作业目录（优先）或本地计算目录；"
                    "系统不会代写或写入提交脚本：\n"
                    + "\n".join(missing))
        script_records: list[dict] = []
        for job, job_local, calc_dir, script_name, source, fingerprint in resolved:
            script_records.append({
                "job_key": job["key"], "attempt_id": job.get("attempt_id"), "source": source,
                "directory": calc_dir if source == "remote" else str(job_local),
                "script_name": script_name, **fingerprint,
            })
        attestations = flow.get("script_attestations") or {}
        attested = all(
            isinstance(attestations.get(item["job_key"]), dict)
            and all(attestations[item["job_key"]].get(key) == item.get(key)
                    for key in ("attempt_id", "source", "directory", "script_name",
                                "normalized_path", "sha256", "size"))
            for item in script_records
        )
        if not attested:
            binding = {
                "operation": "script_attestation",
                "job_key": selected["key"], "attempt_id": selected["attempt_id"],
                "project_id": self.project_id, "task_id": self.task_id,
                "execution_kind": "user_owned_submit_script",
                "scripts": script_records,
                "execution_mode": self._execution_mode(),
            }
            payload = card_payload(
                tool="draft", args={"job_key": selected["key"], "attempt_id": selected["attempt_id"]}, risk="high",
                reason="提交脚本必须由用户显式认领；确认绑定路径、SHA-256、大小和有效期。",
                batch_key="script|" + hashlib.sha256(
                    json.dumps(binding, sort_keys=True).encode()).hexdigest(),
                kind="script_attestation",
                summary="认领以下用户提交脚本（不展示内容）：\n" + "\n".join(
                    f"- {item['job_key']}: `{item['normalized_path']}` "
                    f"({item['size']} B, SHA-256 {item['sha256']})"
                    for item in script_records),
                binding=binding,
            )
            saved = save_card(self.store, self.project_id, self.task_id,
                              self._load_flow(), payload)
            raise PendingConsentError(saved)
        identity = {"job_key": selected["key"], "attempt_id": selected["attempt_id"]}
        precheck_text = self.tool_precheck(identity)
        flow = self._load_flow()
        selected = select_job(flow, identity)
        if not (selected.get("precheck") or {}).get("ok"):
            flow["phase"] = "blocked"
            self._save_flow(flow)
            return precheck_text + "\n[AI_PRECHECK_BLOCKED] 任一必需项缺失，不能生成可提交草稿"
        drafts: list[dict] = []
        lines: list[str] = []
        for job, _job_local, calc_dir, script_name, source, fingerprint in resolved:
            attestation = attestations[job["key"]]
            drafts.append({
                "job_key": job["key"],
                "attempt_id": job["attempt_id"],
                "dir": calc_dir,
                "script_name": script_name,
                "script_source": source,
                "script_sha256": fingerprint["sha256"],
                "script_size": fingerprint["size"],
                "script_path": fingerprint["normalized_path"],
                "attestation_action_id": attestation["action_id"],
                "attestation_binding_hash": attestation["binding_hash"],
                "submit_cmd": " ".join(submit_command(script_name, self.cfg.scheduler_backend)),
            })
            where = "超算作业目录" if source == "remote" else "本地计算目录"
            lines.append(f"- {job['key']}（{job.get('label') or job['key']}）"
                         f"→ 目录 `{calc_dir}`，使用{where}的提交脚本 "
                         f"{script_name}（SHA-256 {fingerprint['sha256']}）")
        selected["draft"] = drafts[0]
        selected["draft"]["scheduler_target"] = selected["precheck"]["snapshot"]["scheduler_target"]
        selected["draft"]["resources"] = {"verification": "human_exact_script", "script_sha256": drafts[0]["script_sha256"]}
        flow["phase"] = "await_submit"
        self._save_flow(flow)
        skipped = [j["key"] for j in jobs
                   if j.get("status") in ("canceled", "skipped")]
        suffix = ("（已跳过：" + "、".join(skipped) + "）") if skipped else ""
        return ("已生成提交草稿（使用用户提供的提交脚本，只校验、未提交）"
                + suffix + "：\n"
                + "\n".join(lines)
                + "\n\n系统会展示绑定当前草稿的一次性提交确认卡；只有用户在卡片中确认后"
                  "才会提交到超算。本次未执行任何 sbatch，也未代写任何脚本。")

    def tool_submit(self, args: dict) -> str:
        flow = self._load_flow()
        from .computation import select_job
        selected = select_job(flow, args)
        if not selected.get("draft"):
            if (flow.get("plan") or {}).get("jobs"):
                return self.tool_draft(args)
            return ToolFailure('TOOL_PRECONDITION_FAILED', "尚未生成提交草稿：请先 plan + draft")
        flow["phase"] = "await_submit"
        self._save_flow(flow)
        return ("已停在「待你确认提交」（红线：我绝不代替你执行 sbatch）。"
                "系统会展示绑定当前草稿的一次性确认卡；卡片批准后才会真实提交。"
                "系统会在每个作业自己的目录里执行 sbatch"
                "（输入文件与提交脚本同目录，绝不在此目录的上一级发起提交）。"
                "未连接超算时系统会如实说明。")

    # ---------------- 作业层级干预（选择提交/跳过，只调规划不提交） ----------------
    def tool_select_jobs(self, args: dict) -> str:
        """按用户要求选择「本次提交哪些作业/跳过哪些」，只调整规划，不提交。"""
        flow = self._load_flow()
        jobs = (flow.get("plan") or {}).get("jobs") or []
        if not jobs:
            return ToolFailure('TOOL_PRECONDITION_FAILED', "尚未规划作业：请先调用 plan 再使用 select_jobs（参考 get_state）")
        submit = [str(x).strip().lower() for x in (args.get("submit") or [])]
        skip = [str(x).strip().lower() for x in (args.get("skip") or [])]
        submit_all = bool(args.get("submit_all"))
        skip_all = bool(args.get("skip_all"))
        if not submit and not skip and not submit_all and not skip_all:
            return ToolFailure('TOOL_PRECONDITION_FAILED', "select_jobs 需要给出选择：提交用 {\"submit\":[\"relax\"]}，"
                    "跳过用 {\"skip\":[\"static\"]}，或 {\"submit_all\":true} / "
                    "{\"skip_all\":true}")

        def _matches(job: dict, terms: list[str]) -> bool:
            key = str(job.get("key") or "").strip().lower()
            label = str(job.get("label") or "").strip().lower()
            for t in terms:
                if t == key or t == label or key in t or t in label:
                    return True
            return False

        changed = set()
        for job in jobs:
            if (job.get("slurm_id") or job.get("submission_state")
                    or job.get("status") in {"completed", "failed", "not_converged", "unknown", "blocked", "running", "queued", "submitted"}):
                continue  # Selection cannot erase an attempt and bypass recovery.
            if skip_all or (skip and _matches(job, skip)):
                if job.get("status") != "skipped":
                    changed.add(job["key"])
                job["status"] = "skipped"
            elif submit_all or _matches(job, submit):
                if job.get("status") in ("skipped", "canceled"):
                    changed.add(job["key"])
                    job["status"] = "draft"
        for job in jobs:
            if job["key"] in changed:
                job.pop("draft", None)
                job.pop("precheck", None)
        from .computation import invalidate
        invalidate(flow, changed, "计算选择已变化；请重新确认")
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

    # ---------------- 作业诊断与恢复（复用真实 Orchestrator 原语） ----------------
    def tool_diagnose_job(self, args: dict) -> str:
        key = args.get("job_key")
        if not isinstance(key, str) or not set(args) <= {"job_key", "attempt_id"}:
            return ToolFailure('AI_DIAGNOSIS_INVALID', "[AI_DIAGNOSIS_INVALID] 需要唯一 job_key")
        flow = self._load_flow()
        job = next((j for j in flow.get("plan", {}).get("jobs", []) if j["key"] == key), None)
        if job is None:
            return ToolFailure('AI_JOB_NOT_FOUND', "[AI_JOB_NOT_FOUND] 作业不存在")
        if args.get("attempt_id") and args["attempt_id"] != job.get("attempt_id"):
            return ToolFailure("ATTEMPT_STALE", "计算尝试已变化，请刷新后重新诊断")
        progress = ""
        if job.get("status") in {"running", "queued", "submitted", "unknown"}:
            progress = self.tool_monitor({})
            flow = self._load_flow()
            job = next(j for j in flow["plan"]["jobs"] if j["key"] == key)
        return json.dumps({"job_key": key, "status": job.get("status"),
                           "slurm_id": job.get("slurm_id"), "diagnosis": job.get("diagnosis"),
                           "attempt_history": job.get("attempt_history", []),
                           "monitor_error": flow.get("monitor_error"),
                           "progress": progress,
                           "recovery": "仅明确失败/未收敛且用户要求时调用 retry_job；unknown 继续查询或人工核对，禁止重提。"}, ensure_ascii=False)

    def tool_retry_job(self, args: dict) -> str:
        key = args.get("job_key")
        if not isinstance(key, str) or not set(args) <= {"job_key", "attempt_id"}:
            return ToolFailure('AI_RETRY_INVALID', "[AI_RETRY_INVALID] 需要唯一 job_key；不接受命令或参数修改")
        flow = self._load_flow()
        job = next((j for j in flow.get("plan", {}).get("jobs", []) if j["key"] == key), None)
        if (not job or job.get("status") not in {"failed", "not_converged"}
                or job.get("submission_state") == "unknown" or not job.get("diagnosis")):
            return ToolFailure('AI_RETRY_BLOCKED', "[AI_RETRY_BLOCKED] 仅有诊断证据的 failed/not_converged 作业可以恢复；unknown/在途/完成作业禁止重提。")
        from .computation import select_job
        if job.get("attempt_id") or args.get("attempt_id"):
            select_job(flow, args, preparing=False)
        binding = {"operation": "retry_job", "project_id": self.project_id,
                   "task_id": self.task_id, "execution_mode": self._execution_mode(),
                   "job_key": key, "attempt_id": job.get("attempt_id"), "job_snapshot": copy.deepcopy(job)}
        payload = card_payload(
            tool="retry_job", args={"job_key": key, "attempt_id": job.get("attempt_id")}, risk="medium", kind="retry_job",
            reason="用户确认后才恢复待准备状态；不提交作业、不修改输出或科学参数。",
            batch_key="retry|" + hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest(),
            summary=f"恢复 {key}（原 Slurm ID {job.get('slurm_id')}，{job['status']}）？\n"
                    + str(job["diagnosis"].get("reason", ""))
                    + "\n保留历史诊断和输出摘要，作废旧草稿与确认。之后必须保全旧输出、重新准备和硬预检，再确认提交。",
            binding=binding)
        raise PendingConsentError(save_card(self.store, self.project_id, self.task_id, flow, payload))

    def tool_monitor(self, args: dict) -> str:
        """内部原语：推进一次真实 squeue 状态（auto_pump / diagnose_job 调用）。

        自本版起已从 _LLM_TOOL_METHODS 移除，LLM 不能再调用它；
        进度查询与状态推进改为流程内部行为（后台监控线程 + 每次消息前的
        自动推进），不再作为智能模式对外工具暴露。
        """
        flow = self._load_flow()
        orch = self._ensure_orch()
        hpc = getattr(orch, "hpc", None)
        if hpc is None:
            return ToolFailure('TOOL_PRECONDITION_FAILED', "未连接超算（未配置 SSH 主机/账号），无法查询作业进度；"
                    "作业若已提交会照常在超算运行。")
        try:
            monitor = getattr(orch, "monitor", None)
            if monitor is None:
                return ToolFailure('TOOL_OPERATION_FAILED', "当前环境不支持监控推进（Orchestrator 缺少 monitor）")
            return monitor(self.store, self.project_id, self.task_id, flow)
        except Exception as exc:  # noqa: BLE001
            return ToolFailure('TOOL_OPERATION_FAILED', f"监控查询失败：{type(exc).__name__}（{exc}）")

    def tool_stop_monitor(self, args: dict) -> str:
        """M56：用户终止当前计算——非终态作业置 canceled、phase=done，
        后台监控与后续提交流程随即停止。"""
        flow = self._load_flow()
        orch = self._ensure_orch()
        stop = getattr(orch, "stop_monitor", None)
        if stop is None:
            return ToolFailure('TOOL_OPERATION_FAILED', "当前环境不支持终止监控（Orchestrator 缺少 stop_monitor）")
        try:
            return stop(self.store, self.project_id, self.task_id,
                        dict(flow or {}))
        except Exception as exc:  # noqa: BLE001
            return ToolFailure('TOOL_OPERATION_FAILED', f"终止失败：{type(exc).__name__}（{exc}）")
