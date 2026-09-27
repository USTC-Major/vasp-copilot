"""下游作业的上游产物交接（系统侧，仅由预检触发）。

依赖链的语义：下游作业的 `POSCAR` 就是上一级的 `CONTCAR`，`ICHARG=1/10/11` 的作业还需要
上一级的 `CHGCAR`，`POTCAR` 与上游同物种、可直接沿用。用户的提交脚本只做 `mpirun`，
所以这些交接由系统在**提交前**完成，而不是写进脚本。

安全边界（逐条都有实现）：
- 源与目标都由**依赖关系**算出（`job["requires"]` + 固定源/目标文件名），不接受模型传路径；
- 只从**已完成**的直接上游作业目录里取，源文件必须落在本任务超算工作区内；
- 目标已存在且与上游不一致时：只有"这份文件本来就是系统上一次交接带进来的、之后没被动过"
  才允许刷新；否则**停下报冲突**，绝不覆盖用户自己的文件；
- 每次搬运都记哈希（源与目标一致才记账），写入 `flow.staged_inputs[job_key]`，并在提交卡上列明。
"""
from __future__ import annotations

import datetime as dt
import posixpath
import re
import shlex

#: 上游产物 → 本作业需要的名字；第三项是"仅当目标 INCAR 需要电荷密度时才搬"。
HANDOFF: tuple[tuple[str, str, bool], ...] = (
    ("POTCAR", "POTCAR", False),
    ("CONTCAR", "POSCAR", False),
    ("CHGCAR", "CHGCAR", True),
)

_ICHARG_RE = re.compile(r"(?im)^\s*ICHARG\s*=\s*(-?\d+)")
_DENSITY_ICHARG = {"1", "10", "11"}
_TEXT_LIMIT = 2 * 1024 * 1024


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def remote_job_dir(hpc, base: str, key: str) -> str:
    """远端作业目录：存在 <base>/<key> 子目录则用之，否则用 base（与执行层同一语义）。"""
    base = str(base or "").rstrip("/")
    stat = getattr(hpc, "stat", None)
    if stat is not None and key:
        try:
            info = stat(f"{base}/{key}")
            if info is not None and info.get("is_file") is not True:
                return f"{base}/{key}"
        except Exception:  # noqa: BLE001
            pass
    return base


def _under(path: str, root: str) -> bool:
    base = posixpath.normpath("/" + str(root or "").lstrip("/")).rstrip("/")
    target = posixpath.normpath("/" + str(path or "").lstrip("/"))
    return bool(base) and (target == base or target.startswith(base + "/"))


def _stat(hpc, path: str) -> dict | None:
    try:
        info = hpc.stat(path)
    except Exception:  # noqa: BLE001
        return None
    return info if isinstance(info, dict) and info.get("is_dir") is not True else None


def _digest(hpc, path: str) -> str:
    """远端 SHA-256：优先让超算自己算（大文件如 CHGCAR 不必回传），失败再回退客户端流式。"""
    try:
        code, out, _err = hpc.run(f"sha256sum -- {shlex.quote(path)}", timeout=300)
        if code == 0:
            token = (out or "").strip().split(" ")[0]
            if re.fullmatch(r"[0-9a-f]{64}", token):
                return token
    except Exception:  # noqa: BLE001
        pass
    return str(hpc.sha256_file(path) or "")


def _needs_density(hpc, job_dir: str) -> bool:
    """目标 INCAR 是否要求读 CHGCAR（ICHARG=1/10/11）。读不到就按"不需要"处理。"""
    path = f"{job_dir}/INCAR"
    if not _stat(hpc, path):
        return False
    try:
        raw = bytes(hpc.read_file(path, max_bytes=_TEXT_LIMIT + 1))
    except Exception:  # noqa: BLE001
        return False
    if len(raw) > _TEXT_LIMIT:
        return False
    found = _ICHARG_RE.search(raw.decode("utf-8", "replace"))
    return bool(found) and found.group(1).lstrip("+") in _DENSITY_ICHARG


def stage_upstream_inputs(flow: dict, cfg, hpc, remote_root: str, job: dict) -> dict:
    """把直接上游作业已完成的产物搬进本作业目录，返回给预检/提交卡展示的小结。"""
    report = {"staged": [], "conflicts": [], "waiting": [], "skipped": []}
    requires = [key for key in (job.get("requires") or []) if key]
    root = str(remote_root or "").rstrip("/")
    if hpc is None or not requires or not root:
        return report
    by_key = {j["key"]: j for j in ((flow.get("plan") or {}).get("jobs") or [])
              if isinstance(j, dict) and j.get("key")}
    pending = [key for key in requires
               if str(by_key.get(key, {}).get("status") or "") != "completed"]
    if pending:
        report["waiting"] = pending
        return report
    target_dir = remote_job_dir(hpc, root, job["key"])
    if not _under(target_dir, root) or not _stat_dir(hpc, target_dir):
        report["skipped"].append({"reason": "作业目录还不存在", "dir": target_dir})
        return report
    upstream = [(key, remote_job_dir(hpc, root, key)) for key in requires]
    upstream = [(key, path) for key, path in upstream
                if _under(path, root) and _stat_dir(hpc, path)]
    if not upstream:
        report["skipped"].append({"reason": "上游作业目录不存在"})
        return report
    previous = {item.get("name"): item for item in
                ((flow.get("staged_inputs") or {}).get(job["key"], {}).get("items") or [])}
    density_needed = _needs_density(hpc, target_dir)
    items: list[dict] = []
    for source_name, target_name, only_if_needed in HANDOFF:
        if only_if_needed and not density_needed:
            continue
        source_dir = next((path for _key, path in upstream
                           if _stat(hpc, f"{path}/{source_name}")), None)
        if source_dir is None:
            report["skipped"].append({"name": target_name,
                                      "reason": f"上游没有 {source_name}"})
            continue
        source = f"{source_dir}/{source_name}"
        target = f"{target_dir}/{target_name}"
        source_stat = _stat(hpc, source)
        source_digest = _digest(hpc, source)
        if not source_stat or not source_digest:
            report["skipped"].append({"name": target_name, "reason": "上游产物读不到"})
            continue
        target_stat = _stat(hpc, target)
        if target_stat:
            target_digest = _digest(hpc, target)
            if target_digest == source_digest:
                items.append({**_item(target_name, source_name, source, target,
                                      source_stat, source_digest), "verified_only": True})
                continue
            recorded = previous.get(target_name)
            refreshable = (isinstance(recorded, dict)
                           and recorded.get("sha256") == target_digest)
            if not refreshable:
                report["conflicts"].append({
                    "name": target_name, "target": target,
                    "target_sha256": target_digest, "source": source,
                    "source_sha256": source_digest, "size": target_stat.get("size")})
                continue
        try:
            code, _out, err = hpc.run(
                f"cp -f -- {shlex.quote(source)} {shlex.quote(target)}", timeout=600)
        except Exception as exc:  # noqa: BLE001
            report["skipped"].append({"name": target_name,
                                      "reason": f"复制失败：{type(exc).__name__}"})
            continue
        written = _stat(hpc, target)
        written_digest = _digest(hpc, target) if written else ""
        if code != 0 or not written or written_digest != source_digest:
            report["skipped"].append({
                "name": target_name,
                "reason": f"复制校验不一致（退出码 {code}{('；' + (err or '').strip()[:120]) if err else ''}）"})
            continue
        items.append(_item(target_name, source_name, source, target, written,
                           written_digest))
        report["staged"].append({"name": target_name, "size": written.get("size"),
                                 "sha256": written_digest})
    if items:
        staged = dict(flow.get("staged_inputs") or {})
        staged[job["key"]] = {
            "job_key": job["key"], "attempt_id": job.get("attempt_id"),
            "upstream": [key for key, _path in upstream], "at": now(),
            "items": items,
        }
        flow["staged_inputs"] = staged
    return report


def _stat_dir(hpc, path: str) -> bool:
    """是不是一个存在的目录（类型信息缺失时按目录处理，与执行层的扁平回退语义一致）。"""
    try:
        info = hpc.stat(path)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(info, dict):
        return False
    if info.get("is_dir") is True:
        return True
    if info.get("is_dir") is False or info.get("is_file") is True:
        return False
    return True


def _item(target_name, source_name, source, target, stat, digest) -> dict:
    return {"name": target_name, "source_name": source_name, "source": source,
            "target": target, "size": int((stat or {}).get("size") or 0),
            "sha256": digest, "staged_at": now()}
