"""Bounded source traversal and byte reads. Never modifies a source path."""
from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path

from backend.input_validation import _ELEMENTS
from ..contracts import ToolboxError


def fail(code, message, status=400, retryable=False):
    raise ToolboxError('POTCAR_' + code, message, status, retryable)


def is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def checked_path(value, *, directory=False):
    if not isinstance(value, str) or not value.strip() or '\x00' in value or len(value) > 4096:
        fail('INVALID_PATH', '请选择后端电脑上的有效绝对目录')
    path = Path(value).expanduser()
    if not path.is_absolute():
        fail('INVALID_PATH', '赝势库路径必须是后端电脑上的绝对路径')
    # Do not resolve first: that would erase evidence of ancestor links/junctions.
    path = Path(os.path.abspath(path))
    try:
        for component in [*reversed(path.parents), path]:
            if is_link(component.lstat()):
                fail('LINK_FORBIDDEN', '赝势库路径或祖先包含链接／重解析点；请选择实体目录')
        if directory and not stat.S_ISDIR(path.lstat().st_mode):
            fail('INVALID_PATH', '赝势库路径不是目录')
    except OSError:
        fail('PATH_UNREACHABLE', '后端电脑无法访问此路径；请检查目录或容器挂载', 400, True)
    return path


def identity(info):
    # Windows lstat and fstat can expose different cached creation times just
    # after a write. File identity, size and modification time remain comparable.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def check_work(deadline, cancel=None):
    if cancel is not None and cancel.is_set():
        fail('SCAN_CANCELLED', '扫描已取消，可以重新扫描', 409, True)
    if time.monotonic() >= deadline:
        fail('SCAN_LIMIT', '扫描超过时间上限；索引未更新，请选择更小的具体集合', 400, True)


def candidate_name(name):
    return 'potcar' in name.casefold()


def enumerate_candidates(root, limits, deadline, cancel=None):
    root = checked_path(str(root), directory=True)
    candidates = []
    stack = [(root, 0)]
    entries = directories = 0
    while stack:
        directory, depth = stack.pop()
        check_work(deadline, cancel)
        checked_path(str(directory), directory=True)
        directories += 1
        if directories > limits.max_directories:
            fail('SCAN_LIMIT', '扫描目录数量超过上限；索引未更新')
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    check_work(deadline, cancel)
                    entries += 1
                    if entries > limits.max_entries:
                        fail('SCAN_LIMIT', '扫描文件数量超过上限；索引未更新')
                    info = entry.stat(follow_symlinks=False)
                    if is_link(info):
                        fail('LINK_FORBIDDEN', '集合内包含链接／重解析点；扫描未发布')
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= limits.max_depth:
                            fail('SCAN_LIMIT', '扫描达到目录深度上限；索引未更新')
                        stack.append((Path(entry.path), depth + 1))
                    elif stat.S_ISREG(info.st_mode) and candidate_name(entry.name):
                        # Windows DirEntry.stat may report inode/device as zero;
                        # use a fresh lstat for the snapshot identity.
                        candidates.append((Path(entry.path), identity(Path(entry.path).lstat())))
                        if len(candidates) > limits.max_candidates:
                            fail('SCAN_LIMIT', '候选数量超过上限；索引未更新')
                    elif candidate_name(entry.name):
                        fail('FILE_UNSUPPORTED', '候选不是普通文件；扫描未发布')
        except OSError:
            fail('PATH_UNREACHABLE', '扫描无法完整读取目录；原索引保留', 400, True)
    return sorted(candidates, key=lambda item: item[0].relative_to(root).as_posix())


def read_source(path, root, limits):
    path = checked_path(str(path))
    root = checked_path(str(root), directory=True)
    if not path.is_relative_to(root):
        fail('PATH_OUTSIDE_LIBRARY', '候选路径超出登记集合')
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            fail('FILE_UNSUPPORTED', '候选不是普通文件')
        if before.st_size > limits.max_input:
            fail('INPUT_LIMIT', '候选超过压缩输入大小上限')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as handle:
            opened = os.fstat(handle.fileno())
            if identity(opened) != identity(before):
                fail('SOURCE_CHANGED', '候选在读取时变化，请重新扫描', 409, True)
            raw = handle.read(limits.max_input + 1)
            after = os.fstat(handle.fileno())
        checked_path(str(path))
        if identity(before) != identity(after) or identity(path.lstat()) != identity(after):
            fail('SOURCE_CHANGED', '候选在读取时变化，请重新扫描', 409, True)
        if len(raw) > limits.max_input:
            fail('INPUT_LIMIT', '候选超过压缩输入大小上限')
        return raw
    except OSError:
        fail('PATH_UNREACHABLE', '无法安全读取候选文件', 400, True)


_VARIANT = re.compile(r'^([A-Z][a-z]?)(?:(?:_[A-Za-z0-9]+)+|(?:\d+(?:\.\d+)?|\.\d+))?$')


def variant_directory(name):
    match = _VARIANT.fullmatch(name)
    return match is not None and match[1] in _ELEMENTS


def collection_paths(root, candidates):
    """Separate parent collections without treating element variants as libraries.

    Each non-element top-level directory containing candidates is a separate
    collection. Never coalesce these into the root, including a single wrapper.
    """
    groups = {}
    root_candidates = []
    for path, _ in candidates:
        relative = path.relative_to(root)
        if len(relative.parts) == 1 or variant_directory(relative.parts[0]):
            root_candidates.append(path)
        else:
            groups.setdefault(relative.parts[0], []).append(path)
    result = [(root / name, len(rows)) for name, rows in sorted(groups.items())]
    if root_candidates and not groups:
        result.insert(0, (root, len(root_candidates)))
    return result
