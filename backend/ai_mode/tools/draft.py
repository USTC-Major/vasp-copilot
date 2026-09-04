"""M9 工具层：提交草稿链路（只生成不执行）。

对齐 WORKFLOW.md v14 §2 步5、MODULE_INTERFACES v1.2 §2（工具调用请求/执行结果回执）：
- 早期 ``SubmissionDraftBuilder`` 仅供隔离的兼容测试/调用方，不暴露给 P0 LLM
  工具或 orchestrator；P0 运行时只接受用户已有脚本并绑定路径、大小与 SHA-256。
- **只生成不执行**：本层从不调用远端执行器/``sbatch``；发令归受限执行器，
  真正提交仍需过 M5 授权门与用户确认（产品红线不放松）。
- 可注入为 M7 Scheduler 的 ``submitter``，实现「先出草稿、确认后再真提交」。
"""
from __future__ import annotations

import json
import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..jobs.state import Job
from .slurm import default_directives, render_sbatch

SUBMIT_BIN = "sbatch"
_DEFAULT_BODY = "srun vasp_std"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit_command(script_name: str) -> list[str]:
    """提交命令（argv 形式，供执行器安全执行；从计算目录内发起）。"""
    return [SUBMIT_BIN, script_name]


def input_fingerprint_local(path: Path) -> dict:
    target = path.resolve()
    if not target.is_file():
        raise ValueError("required input is missing")
    size = target.stat().st_size
    if size <= 0:
        raise ValueError("required input is empty")
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"normalized_path": str(target), "size": size,
            "sha256": digest.hexdigest()}


def input_fingerprint_remote(hpc, path: str) -> dict:
    info = hpc.stat(path)
    if (not isinstance(info, dict) or info.get("is_dir") is True
            or int(info.get("size") or 0) <= 0):
        raise ValueError("required remote input is missing or empty")
    digest = str(hpc.sha256_file(path) or "").lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("required remote input hash is unavailable")
    return {"normalized_path": path, "size": int(info["size"]),
            "sha256": digest}


def precheck_snapshot(*, execution_mode: str, inputs: list[dict],
                      scripts: list[dict]) -> tuple[dict, str]:
    """Canonical immutable snapshot used by precheck and submit consent."""
    snapshot = {
        "execution_mode": execution_mode,
        "inputs": sorted((dict(item) for item in inputs),
                         key=lambda item: (str(item.get("job_key")),
                                           str(item.get("name")))),
        "scripts": sorted((dict(item) for item in scripts),
                          key=lambda item: str(item.get("job_key"))),
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return snapshot, hashlib.sha256(encoded).hexdigest()


@dataclass
class SubmissionDraft:
    """一份「待审提交草稿」。任何字段都不含凭据。"""

    job_id: str
    calc_dir: str                # 超算计算目录（=会话目录）
    script_name: str
    script_text: str
    submit_cmd: list[str]        # 例 ["sbatch", "run.sh"]
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {"job_id": self.job_id, "calc_dir": self.calc_dir,
                "script_name": self.script_name, "script_text": self.script_text,
                "submit_cmd": list(self.submit_cmd),
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: dict) -> "SubmissionDraft":
        return cls(job_id=str(data["job_id"]),
                   calc_dir=str(data.get("calc_dir", "")),
                   script_name=str(data.get("script_name", "run.sh")),
                   script_text=str(data.get("script_text", "")),
                   submit_cmd=list(data.get("submit_cmd", [])),
                   created_at=str(data.get("created_at", _now_iso())))


class SubmissionDraftBuilder:
    """把计算作业渲染为提交草稿（幂等，纯文本生成）。"""

    def __init__(self, *, directives: dict[str, str] | None = None,
                 body: str = _DEFAULT_BODY):
        self.directives = dict(directives or default_directives())
        self.body = body

    def script_name_for(self, job: Job) -> str:
        base = (job.job_id or job.name or "vasp_job").strip().replace(" ", "-")
        return f"submit_{base}.sh"

    _JOB_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")

    def _safe_job_name(self, job: Job) -> str:
        name = (job.name or job.job_id).replace(" ", "-").strip()
        if name and self._JOB_NAME_RE.match(name):
            return name
        fallback = (job.job_id or job.name or "vasp_job").replace(" ", "-")
        return fallback if self._JOB_NAME_RE.match(fallback) else "vasp_job"

    def build(self, job: Job) -> SubmissionDraft:
        """生成草稿（不执行、不写盘）。"""
        if not job or not job.job_id:
            raise ValueError("无效作业：缺少 job_id")
        job_name = self._safe_job_name(job)
        directives = dict(self.directives)
        directives["job-name"] = job_name
        directives["output"] = f"{job.job_id}.out"
        directives["error"] = f"{job.job_id}.err"
        script_name = self.script_name_for(job)
        script_text = render_sbatch(
            directives, body=self.body,
            extra_comments=f"VASP-Doctor ai_mode 提交草稿 job_id={job.job_id}")
        return SubmissionDraft(
            job_id=job.job_id,
            calc_dir=job.workdir,
            script_name=script_name,
            script_text=script_text,
            submit_cmd=submit_command(script_name),
        )

    def write_local(self, draft: SubmissionDraft, *, directory: Path) -> Path:
        """把草稿脚本落到本地预览目录（可选，便于审阅；不影响超算）。"""
        out_dir = Path(directory).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / draft.script_name
        target.write_text(draft.script_text, encoding="utf-8")
        return target


def make_draft_only_submitter(builder: SubmissionDraftBuilder | None = None,
                              *, write_dir: Path | None = None) -> Callable[[Job], None]:
    """构造一个「只生成不执行」的 submitter（供 M7 Scheduler 注入）。

    - 调用即生成草稿；可选把脚本落到本地 ``write_dir`` 预览。
    - 始终返回 ``None``（不真提交，不伪造 slurm_id），保证产品红线由授权门接管。
    """
    builder = builder or SubmissionDraftBuilder()

    def submitter(job: Job) -> None:
        draft = builder.build(job)
        if write_dir is not None:
            builder.write_local(draft, directory=write_dir)
        # 记录到作业 extra，便于后续确认时取出再真提交
        if "draft" not in (job.extra or {}):
            job.extra["draft"] = draft.to_dict()

    return submitter


def resolve_user_submit_script(directory: Path | str) -> Path:
    """在作业目录里定位「用户自己提供」的唯一提交脚本（*.sh）。

    产品红线：提交脚本必须由用户手动提供，系统/AI 绝不代写或生成 ``*.sh``。
    本函数只认作业目录里恰好那一个 ``*.sh``：
    - 缺失：raise ``RuntimeError``（调用方据此进阻塞态并提示用户补齐）；
    - 多个：raise ``RuntimeError``（提示只保留唯一脚本，避免提交歧义）。
    """
    base = Path(directory).expanduser().resolve()
    try:
        scripts = sorted(
            p for p in base.iterdir()
            if p.is_file() and p.suffix.lower() == ".sh"
        )
    except OSError:
        scripts = []
    if not scripts:
        raise RuntimeError(
            "该作业目录里没有用户提供的提交脚本（*.sh），无法生成提交草稿；"
            "请把唯一的 *.sh（如 run.sh）放进该作业目录。")
    if len(scripts) > 1:
        raise RuntimeError(
            "该作业目录里有多个提交脚本（" + "、".join(p.name for p in scripts)
            + "），请只保留唯一的一个 *.sh。")
    return scripts[0]


def find_remote_submit_script(hpc, remote_dir: str) -> str | None:
    """在超算作业目录里定位「用户自己提供」的唯一提交脚本（*.sh）。

    M51 起超算为主战场：脚本放在远端作业目录即可被 draft/提交直接使用。
    红线不放松：脚本必须由用户提供并经一次性确认 action 显式认领；本函数
    只负责定位，不生成、不修改。
    - 恰有一个：返回脚本名（相对 remote_dir）；
    - 没有任何 *.sh：返回 None（调用方回退本地或进阻塞态）；
    - 多个：raise ``RuntimeError``（唯一性要求，避免提交歧义）；
    - 目录不存在/不可读：按没有处理（返回 None）。
    """
    try:
        infos = hpc.list_dir_info(remote_dir)
    except Exception:  # noqa: BLE001
        return None
    names = sorted(
        str(i.get("name") or "") for i in (infos or [])
        if isinstance(i, dict) and not i.get("is_dir")
        and str(i.get("name") or "").lower().endswith(".sh"))
    if len(names) > 1:
        raise RuntimeError(
            "超算作业目录里有多个提交脚本（" + "、".join(names)
            + "），请只保留唯一的一个 *.sh。")
    return names[0] if names else None


def fingerprint_local_submit_script(path: Path | str) -> dict:
    """Hash a user-owned local script without exposing its contents."""
    script = Path(path).expanduser().resolve()
    if not script.is_file() or script.suffix.lower() != ".sh":
        raise RuntimeError("提交脚本不存在或不是 *.sh")
    digest = hashlib.sha256()
    size = 0
    with script.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    if size <= 0:
        raise RuntimeError("提交脚本为空")
    return {"normalized_path": str(script), "sha256": digest.hexdigest(),
            "size": size, "mtime": script.stat().st_mtime_ns}


def fingerprint_remote_submit_script(hpc, remote_dir: str,
                                     script_name: str) -> dict:
    """Stream-hash the exact remote script selected for submission."""
    if (not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\.sh", script_name)
            or "/" in script_name or "\\" in script_name):
        raise RuntimeError("非法提交脚本名")
    directory = posixpath.normpath("/" + str(remote_dir).lstrip("/"))
    remote_path = posixpath.join(directory, script_name)
    stat = hpc.stat(remote_path)
    if stat is None:
        raise RuntimeError("远端提交脚本不存在")
    size = int(stat.get("size") or 0)
    if size <= 0:
        raise RuntimeError("远端提交脚本为空")
    if hasattr(hpc, "sha256_file"):
        digest = hpc.sha256_file(remote_path)
    else:
        read_cap = size + 1 if size > 0 else 1024 * 1024
        data = bytes(hpc.read_file(remote_path, max_bytes=read_cap))
        if len(data) != size and size:
            raise RuntimeError("远端提交脚本读取不完整")
        digest = hashlib.sha256(data).hexdigest()
        size = len(data)
    return {"normalized_path": remote_path, "sha256": digest,
            "size": size, "mtime": int(stat.get("mtime") or 0)}
