"""M11 项目级额外设置：计算任务运行时要求/指引随项目走，本地化存储。

对齐 AI_MODE_WORKFLOW.md v14 §10 与 MASTER 总纲 §五。
- 额外设置 = 一组「纯内容条目」（无名字、只有内容，可写任意文字），
  作为 AI 控制计算任务运行时的要求与指引（项目 ▸ 额外设置）。
- 这些条目在每次对话时从磁盘实时注入 agent 的 system prompt，不属于
  聊天记录，因此不会被聊天上下文裁剪/覆盖。
- 兼容旧形态（{job_type: [条目]} / {job_type: {note, params}}）→
  归一化时折叠成纯内容行。
- 本地只存 ~/.vasp-ai/projects/<project_id>.json（VASP_AI_HOME 可覆盖目录）；
  私人信息（密钥/密码等）一律不得写入项目设置。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .. import paths

PROJECTS_DIRNAME = "projects"
_SAFE_ID = re.compile(r"[^\w\-.]+")
#: 内容条目若命中这些敏感模式即拒写（红线：密钥/口令不落盘）。
_SENSITIVE_CONTENT = ("password", "passwd", "secret", "credential",
                      "api_key", "private key", "access token", "bearer")


class ProjectSettingsError(ValueError):
    """项目设置不合法（含敏感信息/空条目）。"""


def sanitize_project_id(raw: str, *, fallback: str = "misc") -> str:
    """把任意 project_id 归一化为安全文件基名（默认 fallback）。"""
    cleaned = _SAFE_ID.sub("-", str(raw or "")).strip(".-")
    while ".." in cleaned:
        cleaned = cleaned.replace("..", ".")
    cleaned = cleaned.strip(".-")
    return cleaned or fallback


def _legacy_dict_to_entries(accuracy: Mapping) -> list[str]:
    """把旧形态 {job_type: {note, params}} / [条目] 折叠成纯内容行。"""
    entries: list[str] = []
    for job_type in sorted(accuracy):
        value = accuracy[job_type]
        if isinstance(value, Mapping):
            note = str(value.get("note") or "").strip()
            params = value.get("params") if isinstance(value.get("params"), list) else []
        elif isinstance(value, list):
            note = ""
            params = value
        else:
            continue
        seg = f"任务类型「{job_type}」"
        if note:
            seg += f"：{note}"
        if params:
            bits: list[str] = []
            for entry in params:
                if not isinstance(entry, Mapping):
                    continue
                key = str(entry.get("key") or "").strip()
                val = str(entry.get("value") or "").strip()
                enote = str(entry.get("note") or "").strip()
                if not key:
                    continue
                bit = f"{key} = {val}" if val else key
                if enote:
                    bit += f"（{enote}）"
                bits.append(bit)
            if bits:
                seg += "；" + "；".join(bits)
        entries.append(seg)
    return entries


def normalize_accuracy(accuracy: Any) -> list[str]:
    """把 accuracy 归一化为纯内容条目列表（无名字，只有内容，可写任意文字）。

    新形态：["...", "..."]；
    兼容旧形态 {job_type: [条目]} / {job_type: {note, params}} → 折叠成内容行。
    """
    if isinstance(accuracy, list):
        return [str(x).strip() for x in accuracy if str(x).strip()]
    if isinstance(accuracy, Mapping):
        return _legacy_dict_to_entries(accuracy)
    return []


def render_accuracy_text(accuracy: Any) -> str:
    """把项目额外设置渲染成给 AI 的「运行时要求与指引」文本（空=未配置）。

    这些条目是用户为控制本项目计算任务运行时而配置的要求/指引；每次对话实时
    从磁盘注入 system prompt，不属于聊天记录，不会被聊天上下文裁剪或覆盖。
    与科学正确性或用户当前明确指令冲突时说明理由并先与用户确认，绝不盲目照搬。
    """
    entries = normalize_accuracy(accuracy)
    if not entries:
        return ""
    lines = [
        "【本项目计算任务设置 · 用户配置的运行时要求与指引】",
        "以下条目是用户为控制本计算任务运行时而配置的要求与指引，每一条只有内容、没有"
        "名字。你规划作业、准备输入、判断与提交、监控与报告时，都应把这些条目作为约束"
        "认真对照执行；它们会在你每次询问时从磁盘实时注入本系统提示，不属于聊天记录，"
        "不会被聊天上下文裁剪或覆盖。当用户询问当前计算的参数/精度设置（如 ENCUT、收敛"
        "标准等）时，以这里列出的条目和用户最近在「额外设置」里配置的内容为准如实回答，"
        "不要只依据工作区里已生成的输入文件；发现条目与已生成输入不一致时，指出差异并按"
        "用户最新配置的意图处理。若某条目与科学正确性或用户当前最明确的指令冲突，"
        "说明理由并与用户确认后再执行，绝不盲目照搬。",
    ]
    for index, entry in enumerate(entries, 1):
        lines.append(f"{index}. {entry}")
    return "\n".join(lines)


def validate_accuracy(accuracy: Any) -> list[str]:
    """检查额外设置结构。返回问题清单（空=合法）。

    结构：字符串条目列表（每条即一条要求/指引内容）。
    - 非 list / 非字符串条目 / 空条目 → 问题。
    - 内容疑似含敏感信息（密钥/口令等不得写入项目设置）→ 问题。
    """
    issues: list[str] = []
    if not isinstance(accuracy, list):
        return ["accuracy 需为字符串条目列表（每条即一条要求/指引内容）"]
    for idx, entry in enumerate(accuracy):
        if not isinstance(entry, str):
            issues.append(f"条目[{idx}]: 需为字符串内容")
            continue
        text = entry.strip()
        if not text:
            issues.append(f"条目[{idx}]: 内容不能为空")
            continue
        lower = text.lower()
        if any(s in lower for s in _SENSITIVE_CONTENT):
            issues.append(f"条目[{idx}]: 内容疑似含敏感信息（密钥/口令等不得写入项目设置）")
    return issues


def require_valid_accuracy(accuracy: Any) -> None:
    issues = validate_accuracy(accuracy)
    if issues:
        raise ProjectSettingsError("; ".join(issues))


def project_settings_path(project_id: str, root: Path | None = None) -> Path:
    """项目设置文件路径：``<root>/projects/<sanitized>.json``。"""
    base = Path(root).expanduser().resolve() if root else paths.home_dir()
    return base / PROJECTS_DIRNAME / f"{sanitize_project_id(project_id)}.json"


class ProjectSettingsStore:
    """项目额外设置（计算任务运行时要求/指引条目）存储。只写本地数据目录。"""

    def __init__(self, root: Path | None = None):
        self.root = Path(root).expanduser().resolve() if root else paths.home_dir()
        self.dir = self.root / PROJECTS_DIRNAME

    def _path(self, project_id: str) -> Path:
        return project_settings_path(project_id, root=self.root)

    def load(self, project_id: str) -> dict:
        pid = sanitize_project_id(project_id)
        empty = {"project_id": pid, "accuracy": []}
        path = self._path(pid)
        if not path.is_file():
            return empty
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(data, dict):
            return empty
        data.setdefault("project_id", pid)
        data["accuracy"] = normalize_accuracy(data.get("accuracy") or [])
        return data

    def save(self, project_id: str, accuracy: Any) -> dict:
        require_valid_accuracy(accuracy)
        pid = sanitize_project_id(project_id)
        payload = {"project_id": pid, "accuracy": normalize_accuracy(accuracy)}
        path = self._path(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        return payload

    def delete(self, project_id: str) -> bool:
        path = self._path(project_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def list_all(self) -> list[dict]:
        out: list[dict] = []
        if self.dir.exists():
            for item in sorted(self.dir.iterdir()):
                if item.suffix == ".json":
                    out.append(self.load(item.stem))
        return out