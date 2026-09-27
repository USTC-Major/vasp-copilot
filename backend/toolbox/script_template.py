"""脚本模板一致 ⇒ 自动认领（只做哈希比对，不写文件、不改脚本）。

用户在智能设置里填好「提交脚本模板」（超算上的绝对路径）并打开复制开关，就等于授权：
**凡是与这份模板逐字节一致的作业脚本，直接视为已认领**。

这替代了原来"复制完成/手工放置后还要再点一次认领"的步骤；安全性质不变：
- 只比对 SHA-256 与大小，不读取、不修改脚本内容，也不生成任何文件；
- 模板或脚本任一变化，哈希就对不上，不会误认领；
- 提交作业仍要用户单独确认那张提交卡。
"""
from __future__ import annotations

import datetime as dt

from .config import as_bool, normalize_submit_script_template

_FIELDS = ("attempt_id", "source", "directory", "script_name",
           "normalized_path", "sha256", "size")


def stamp_template_attestations(flow: dict, cfg, hpc, records) -> list[str]:
    """把与用户模板逐字节一致的远端脚本登记为「已认领」，返回补上的 job_key 列表。"""
    if hpc is None or not as_bool(getattr(cfg, "allow_script_deploy", False)):
        return []
    template = normalize_submit_script_template(
        getattr(cfg, "submit_script_template", ""))
    if not template:
        return []
    try:
        stat = hpc.stat(template)
        if not isinstance(stat, dict) or stat.get("is_dir") is True:
            return []
        template_digest = str(hpc.sha256_file(template) or "")
        template_size = int(stat.get("size") or 0)
    except Exception:  # noqa: BLE001 - 读不到模板就按"没有配置"处理
        return []
    if not template_digest or template_size <= 0:
        return []
    attestations = dict(flow.get("script_attestations") or {})
    stamped: list[str] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        key = str(record.get("job_key") or "")
        if (not key or str(record.get("source") or "") != "remote"
                or record.get("sha256") != template_digest
                or int(record.get("size") or 0) != template_size):
            continue
        attestations[key] = {
            "job_key": key,
            **{field: record.get(field) for field in _FIELDS},
            "sha256": template_digest,
            "size": template_size,
            "action_id": str(record.get("action_id") or ""),
            # 自动认领没有对应的人工卡片；留空字段保持与手工认领同构。
            "binding_hash": "",
            "claimed_by": "template_match",
            "template_path": template,
            "claimed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        stamped.append(key)
    if stamped:
        flow["script_attestations"] = attestations
    return stamped
