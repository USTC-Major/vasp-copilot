"""M9 工具层：SLURM 提交模板与 sbatch 指令白名单。

对齐 WORKFLOW.md v14 §2 步5/6、MODULE_INTERFACES v1.2 §1.6：
- 本模块只**生成**提交脚本文本，绝不执行 ``sbatch``（执行走受限执行器/授权门）。
- 指令走白名单：未知键一律拒绝；值做安全校验（正整数/时间/安全文件名等），
  拒绝换行、分号、管道、``$()`` 等可注入字符。
"""
from __future__ import annotations

import re
from typing import Callable, Mapping

_INT_RE = re.compile(r"^[1-9]\d*$")
_TIME_RE = re.compile(r"^(\d+-)?\d{1,2}:\d{2}:\d{2}$")
_MEM_RE = re.compile(r"^[1-9]\d*[KMGTP]?$")
_IDENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")
_FILE_RE = re.compile(r"^[\w.\-/]{1,200}$")


def _ident(v: str) -> bool:
    return bool(_IDENT_RE.match(v)) and ".." not in v


#: 允许键（去 ``--`` 前缀后的规范名） -> 值校验函数。白名单：未知键即拒。
DIRECTIVE_ALLOWLIST: dict[str, Callable[[str], bool]] = {
    "job-name": _ident,
    "partition": _ident,
    "account": _ident,
    "qos": _ident,
    "constraint": _ident,
    "nodes": lambda v: bool(_INT_RE.match(v)),
    "ntasks": lambda v: bool(_INT_RE.match(v)),
    "ntasks-per-node": lambda v: bool(_INT_RE.match(v)),
    "cpus-per-task": lambda v: bool(_INT_RE.match(v)),
    "gpus": lambda v: bool(_INT_RE.match(v)),
    "time": lambda v: bool(_TIME_RE.match(v or "")),
    "mem": lambda v: bool(_MEM_RE.match(v or "")),
    "mem-per-cpu": lambda v: bool(_MEM_RE.match(v or "")),
    "output": lambda v: bool(_FILE_RE.match(v or ""))
              and not v.startswith(("/", "~")) and ".." not in v,
    "error": lambda v: bool(_FILE_RE.match(v or ""))
              and not v.startswith(("/", "~")) and ".." not in v,
    "gres": lambda v: bool(re.match(r"^[\w:./\-]{1,100}$", v or "")),
    "mail-type": lambda v: v in {"BEGIN", "END", "FAIL", "ALL", "TIME_LIMIT"},
    "mail-user": lambda v: bool(re.match(r"^[\w.+-]+@[\w.-]+$", v or "")),
    "dependency": lambda v: bool(re.match(r"^[\w:,]+$", v or "")),
}


def validate_directives(directives: Mapping[str, str]) -> list[str]:
    """检查指令字典，返回问题清单（空=合法）。

    - 未知键（不在白名单）→ 问题。
    - 值不合法（含注入字符/非法形态）→ 问题。
    """
    issues: list[str] = []
    for key, raw in (directives or {}).items():
        norm = key[2:] if key.startswith("--") else key
        checker = DIRECTIVE_ALLOWLIST.get(norm)
        if checker is None:
            issues.append(f"未知 sbatch 指令: --{norm}")
            continue
        value = str(raw)
        if any(ch in value for ch in "\r\n;|&`"):
            issues.append(f"非法指令值(含控制字符): --{norm}={value!r}")
        elif not checker(value):
            issues.append(f"非法指令值: --{norm}={value!r}")
    return issues


def sanitize_text(text: str) -> str:
    """去掉脚本主体中的回程符（正常业务透传换行）。"""
    return (text or "").replace("\r", "")


def render_sbatch(directives: Mapping[str, str], *, body: str = "",
                  extra_comments: str = "") -> str:
    """渲染一份 sbatch 提交脚本文本。不写盘、不执行。

    :param directives: 指令字典（键可为带 ``--`` 前缀或不带）。
    :param body: 脚本主体（如 ``srun vasp_std``）。
    :param extra_comments: 附加注释（如来自哪个作业）。
    :raises ValueError: 指令含未知键或不合法值。
    """
    issues = validate_directives(directives)
    if issues:
        raise ValueError("; ".join(issues))
    lines = ["#!/bin/bash"]
    for comment_line in (extra_comments or "").splitlines():
        lines.append(f"# {comment_line}".rstrip())
    for key in (directives or {}):
        norm = key[2:] if key.startswith("--") else key
        lines.append(f"#SBATCH --{norm}={directives[key]}")
    clean_body = sanitize_text(body).strip("\n")
    if clean_body:
        lines.append("")
        lines.append(clean_body)
    return "\n".join(lines) + "\n"


DEFAULT_DIRECTIVES: dict[str, str] = {
    "job-name": "vasp_job",
    "nodes": "1",
    "time": "12:00:00",
    "output": "run.out",
    "error": "run.err",
}




def default_directives(job_name: str | None = None) -> dict[str, str]:
    """返回默认指令副本（可覆盖 job-name）。"""
    d = dict(DEFAULT_DIRECTIVES)
    if job_name:
        d["job-name"] = job_name
    return d
