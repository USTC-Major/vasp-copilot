# -*- coding: utf-8 -*-
"""本地工作区只读快照：LLM 对话态下让模型始终能看到工作区内容。

对齐 WORKFLOW v14 §3「中枢双态」：对话态闲聊时用户可能让 LLM 查看任务
工作区（INCAR/POSCAR/日志等）。本模块把本地工作区做成一段「每次消息实时
生成的只读快照」注入 LLM 上下文，让模型始终读到工作区，而不是一问三不知。
- 只读：绝不写文件、不执行命令、不越过工作区目录（解析真实路径后遍历）。
- 有界：目录深度/条目数/单个文件预览/预览总长度全部封顶，防上下文爆炸。
- 紧凑（M40）：只列计算方法关键文件与目录概览，无关/二进制/工程临时文件
  （Office 锁文件、MS 工程 XML/Log、参考文档、压缩包、三维模型等）不进快照；
  大目录折叠为一行概览。模型需要细节时可另调 ws_read 读取具体文件，仍可
  全程读取/操作工作区，只是不再往上下文塞一大段无意义的文件清单。
- 跳过隐藏目录与依赖/构建目录，跳过二进制文件；不做任何网络/SSH 操作。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath

logger = logging.getLogger("ai_mode.workspace")

#: 遍历时跳过的一级目录（依赖/构建/版本/缓存/工程噪音）与隐藏目录，
#: 避免上下文被无关文件淹没。名称在任何层级的目录下都生效。
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
    ".idea", "dist", "build", ".pytest_cache", "site-packages", "scripts",
    "logs", "data", ".tox", ".mypy_cache", ".ruff_cache",
    # M40：常见工程/文档噪音目录（Materials Studio 工程、参考资料、备份/缓存等）
    "Modules", "Documents", "Examples", "Files", "附件", "参考", "References",
    "Pictures", "Images", "SCREENS", "backup", "backups", "temp", "tmp",
    "cache", "Caches", "__MACOSX", "Thumbs",
})

#: 快照中始终高优先列出的 VASP/计算相关基准文件名（按相对路径的 basename 匹配）。
_PRIORITY_FILES = frozenset({
    "POSCAR", "CONTCAR", "INCAR", "KPOINTS", "POTCAR", "POTCAR.spec",
    "WAVECAR", "CHGCAR", "AECCAR0", "AECCAR1", "AECCAR2", "LOCPOT",
    "ELFCAR", "PROCAR", "DOSCAR", "EIGENVAL", "PCDAT", "OUTCAR", "OSZICAR",
    "vasprun.xml", "vasprun", "run.sh", "submit.sh", "job.sh",
})

#: 按相对路径前缀高优先的目录（这些目录下的文件整体视为计算关键文件）。
_PRIORITY_DIR_PREFIXES = (
    "poscar/", "potcar/", "incar/", "kpoints/", "cif/", "structure/",
    "计算结果/", "results/", "dos/",
)

#: 高优先文件的扩展名（VASP 输入/结果、结构、数据文件）。
_PRIORITY_SUFFIXES = (".vasp", ".incar", ".kpoints", ".potcar", ".cif",
                      ".dat", ".poscar", ".contcar")

#: 快照中整体跳过的文件扩展名（二进制/办公/媒体/压缩/三维模型等，需时可 ws_read 再读）。
_SKIP_EXTS = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".stp", ".step", ".stl", ".png", ".jpg", ".jpeg", ".gif", ".bmp",
    ".tif", ".tiff", ".ico", ".zip", ".rar", ".7z", ".gz", ".tar", ".bz2",
    ".exe", ".dll", ".so", ".dylib", ".lnk", ".tmp", ".bak", ".pyc",
})


def _is_text_file(data: bytes) -> bool:
    """粗略判断是否为文本（前 1KB 无 NUL 字节即可认为可读）。"""
    return b"\x00" not in (data[:1024])


def _include_file(name: str) -> bool:
    """是否应进入快照（过滤隐藏/临时/工程噪音/二进制级扩展名）。"""
    low = name.lower()
    if name.startswith(".") or name.startswith("~$") or low.startswith(".~lock"):
        return False
    if low in ("thumbs.db", "desktop.ini", ".ds_store"):
        return False
    return PurePosixPath(name).suffix.lower() not in _SKIP_EXTS


def _is_priority(rel: str) -> bool:
    """是否为计算方法关键文件（相对路径）。"""
    low = rel.lower()
    base = PurePosixPath(rel).name
    if base in _PRIORITY_FILES or base.lower() in _PRIORITY_FILES:
        return True
    if any(low.startswith(prefix) for prefix in _PRIORITY_DIR_PREFIXES):
        return True
    return low.endswith(_PRIORITY_SUFFIXES)


def snapshot_workspace(root, *, max_depth: int = 3, max_entries: int = 150,
                       max_preview_bytes: int = 4096,
                       preview_total_cap: int = 12000,
                       total_cap: int = 16000,
                       non_prio_group_files: int = 2,
                       group_examples: int = 3) -> tuple[bool, str]:
    """生成工作区只读快照文本（真实读盘，每次调用都是最新状态、紧凑有界）。

    :param root: 本地工作区路径（字符串或 Path）。空/不可访问时返回说明行。
    :param max_entries: 快照中最多列出的文件/目录行数（折叠行按 1 行计）。
    :param max_preview_bytes: 单个文件上屏预览的字节上限（仅关键文件预览）。
    :param preview_total_cap: 全部预览累计字符上限。
    :param total_cap: 整段快照字符上限（超出截断并标注）。
    :param non_prio_group_files: 非关键文件在某目录内达到该数量即折叠成一行概览。
    :param group_examples: 折叠行里示例文件名数量。
    :return: (是否找到可读工作区, 快照文本)。文本始终非空且有界。
    """
    if root is None or not str(root).strip():
        return False, "（本地工作区未设置）"
    path = Path(str(root).strip())
    try:
        resolved = path.expanduser().resolve()
    except OSError as exc:
        logger.warning("工作区路径无法解析 %r: %s", str(path), exc)
        return True, f"（本地工作区路径无法解析：{str(path)}）"
    if not resolved.is_dir():
        return True, f"（本地工作区不可访问或不是目录：{str(path)}）"

    rel_len = len(resolved.parts)
    entries: list[tuple[str, int, bool]] = []  # (rel, size, priority)
    skipped_files = 0
    try:
        for dirpath, dirnames, filenames in os.walk(resolved, topdown=True):
            depth = len(Path(dirpath).parts) - rel_len
            if depth > max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith(".")
                # M40：Materials Studio 等工程惯用后缀目录整体跳过
                and not d.lower().endswith(("_files", "_docs", "_attachments"))
            )
            for name in sorted(filenames):
                if not _include_file(name):
                    skipped_files += 1
                    continue
                fp = Path(dirpath) / name
                if not fp.is_file():
                    continue
                try:
                    size = fp.stat().st_size
                except OSError:
                    continue
                rel = fp.relative_to(resolved).as_posix()
                entries.append((rel, size, _is_priority(rel)))
    except OSError as exc:
        logger.warning("工作区快照失败 %r: %s", str(resolved), exc)
        return True, f"（读取工作区时出错：{type(exc).__name__}）"

    # 关键文件排在前面；其余按目录分组，小目录列明细、大目录折叠成一行。
    prio = sorted((it[:2] for it in entries if it[2]), key=lambda it: it[0])
    groups: dict[str, list[tuple[str, int]]] = {}
    for rel, size, is_prio in entries:
        if is_prio:
            continue
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        groups.setdefault(parent, []).append((rel, size))
    for key in groups:
        groups[key].sort(key=lambda it: it[0])

    tree: list[str] = [f"[工作区快照] {str(resolved)}"]
    if skipped_files:
        tree.append(f"（已过滤 {skipped_files} 个工程临时/二进制/无关文件）")
    shown = 0
    for rel, size in prio:
        if shown >= max_entries:
            break
        tree.append(f"- {rel}（{size} B）")
        shown += 1
    for parent in sorted(groups):
        if shown >= max_entries:
            break
        items = groups[parent]
        if len(items) <= non_prio_group_files:
            for rel, size in items:
                if shown >= max_entries:
                    break
                tree.append(f"- {rel}（{size} B）")
                shown += 1
        else:
            folder = (parent + "/") if parent else "（根目录）"
            examples = "、".join(PurePosixPath(rel).name
                                 for rel, _size in items[:group_examples])
            tree.append(
                f"- {folder}…（该目录共 {len(items)} 个非关键文件，"
                f"示例：{examples}…，已省略）")
            shown += 1

    # 预览：仅计算方法关键文件的小文本文件（其余用 ws_read 按需读取）。
    previews: list[str] = []
    preview_chars = 0
    for rel, size in prio:
        if not (0 < size <= max_preview_bytes) or preview_chars >= preview_total_cap:
            continue
        fp = resolved / rel
        try:
            data = fp.read_bytes()
        except OSError:
            continue
        if not data or not _is_text_file(data):
            continue
        text = data.decode("utf-8", "replace")
        if len(text) > max_preview_bytes:
            text = text[:max_preview_bytes]
        preview_chars += len(rel) + len(text)
        previews.append(f"--- 文件预览: {rel} ---\n{text}")

    body = "\n".join(tree)
    if previews:
        body += "\n\n" + "\n\n".join(previews)
    if len(body) > total_cap:
        body = body[:total_cap] + "\n（快照过大已截断）"
    return True, body


def snapshot_hpc_workspace(hpc, hpc_dir, *, max_depth: int = 3,
                           max_entries: int = 150,
                           max_preview_bytes: int = 2048,
                           preview_total_cap: int = 6000,
                           total_cap: int = 12000) -> tuple[bool, str]:
    """超算工作区（远端）只读快照：经 SSH/SFTP 实时列目录，紧凑有界。

    与本地 snapshot_workspace 同一套过滤/优先级规则（复用 _include_file/
    _is_priority/SKIP_DIRS）；目录遍历走 hpc.list_dir_info，关键小文件预览
    走 hpc.read_file（仅前 max_preview_bytes）。任何 SSH/SFTP 异常都转成
    说明文本返回，绝不抛出、绝不写远端、绝不执行远端命令。
    :param hpc: SSHManager（或同签名的假对象）；None 表示未连接超算。
    :param hpc_dir: 超算工作区根目录（远端绝对路径）。
    :return: (是否生成出实质内容, 快照文本)。文本始终非空且有界。
    """
    if hpc is None:
        return False, "（未连接超算：请到「设置 → SSH」配置主机/账号后重试）"
    root = str(hpc_dir or "").strip().rstrip("/")
    if not root:
        return False, "（超算工作区未设置）"

    entries: list[tuple[str, int, bool]] = []   # (rel, size, priority)
    skipped_files = 0
    top_failed: str = ""

    def _walk(remote: str, rel: str, depth: int) -> None:
        nonlocal skipped_files, top_failed
        if depth > max_depth:
            return
        try:
            infos = hpc.list_dir_info(remote or ".")
        except Exception as exc:  # noqa: BLE001 - SSH/SFTP 异常如实降级
            logger.warning("超算目录列举失败 %r: %s", remote, exc)
            if depth == 1:
                top_failed = type(exc).__name__
            return
        for info in infos:
            name = str(info.get("name") or "")
            is_dir = bool(info.get("is_dir"))
            if is_dir:
                # 目录：跳过隐藏/依赖/工程噪音（与本地一致，含 _files 后缀）
                if (name.startswith(".") or name in SKIP_DIRS
                        or name.lower().endswith(
                            ("_files", "_docs", "_attachments"))):
                    continue
                _walk(f"{remote}/{name}",
                      f"{rel}/{name}" if rel else name, depth + 1)
                continue
            if not _include_file(name):
                skipped_files += 1
                continue
            child_rel = f"{rel}/{name}" if rel else name
            entries.append((child_rel, int(info.get("size") or 0),
                            _is_priority(child_rel)))

    _walk(root, "", 1)
    if top_failed and not entries:
        return True, f"（超算工作区不可访问：{top_failed}，目录 {root}）"

    prio = sorted((it[:2] for it in entries if it[2]), key=lambda it: it[0])
    groups: dict[str, list[tuple[str, int]]] = {}
    for rel, size, is_prio in entries:
        if is_prio:
            continue
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        groups.setdefault(parent, []).append((rel, size))
    for key in groups:
        groups[key].sort(key=lambda it: it[0])

    tree: list[str] = [f"[超算工作区快照] {root}"]
    if skipped_files:
        tree.append(f"（已过滤 {skipped_files} 个隐藏/二进制/无关文件）")
    shown = 0
    for rel, size in prio:
        if shown >= max_entries:
            break
        tree.append(f"- {rel}（{size} B）")
        shown += 1
    for parent in sorted(groups):
        if shown >= max_entries:
            break
        items = groups[parent]
        for rel, size in items:
            if shown >= max_entries:
                break
            tree.append(f"- {rel}（{size} B）")
            shown += 1
    if not entries:
        tree.append("（目录为空或无可见文件）")

    # 预览：仅关键小文本文件（经 SFTP 读取，同样有界）。
    previews: list[str] = []
    preview_chars = 0
    for rel, size in prio:
        if not (0 < size <= max_preview_bytes) or preview_chars >= preview_total_cap:
            continue
        try:
            data = hpc.read_file(f"{root}/{rel}", max_bytes=max_preview_bytes)
        except Exception:  # noqa: BLE001
            continue
        if not data or not _is_text_file(bytes(data)):
            continue
        text = bytes(data).decode("utf-8", "replace")
        if len(text) > max_preview_bytes:
            text = text[:max_preview_bytes]
        preview_chars += len(rel) + len(text)
        previews.append(f"--- 超算文件预览: {rel} ---\n{text}")

    body = "\n".join(tree)
    if previews:
        body += "\n\n" + "\n\n".join(previews)
    if len(body) > total_cap:
        body = body[:total_cap] + "\n（快照过大已截断）"
    return True, body