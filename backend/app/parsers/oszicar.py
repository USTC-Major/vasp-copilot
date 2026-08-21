from __future__ import annotations

import re
from typing import Any, Optional

from ..schemas.parsed import ElectronicStep, OszicarData

# 电子迭代算法标签白名单（含真实 OSZICAR 常见的 DMP/SDA）。
# 严格行首锚定的"白名单标签 + 冒号 + 步号"，避免普通文本误识别。
_ELEC_RE = re.compile(r"^\s*(DAV|RMM|CG|DMP|SDA)\s*:\s*(\d+)\s*(.*)$")
# 形似"大写单词:数字"的候选行：仅用于记录简短 warning，不做解析。
_UNKNOWN_TAG_RE = re.compile(r"^\s*([A-Z]{2,5})\s*:\s*\d+")
# 离子步汇总行：仅含 F= 的数字开头行才算离子步（表头等不算）。
_IONIC_RE = re.compile(r"^\s*(\d+)\s+F\s*=")
# 离子汇总尾部 key=value token（F=、E0=、d E=、mag= 等）。
_TOKEN_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z0-9]+)*)\s*=\s*(-?[\d.]+(?:[EeDd][-+]?\d+)?)")
# 数值 token：支持 E/e/D/d 科学计数法、正负号、小数与前导小数点。
_NUM_RE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?$")
_INT_RE = re.compile(r"^[-+]?\d+$")

# 电子行尾部列序（按原始 token 位置解析；缺失/星号溢出列置 None，后续列不前移）。
_ELEC_COLUMNS = ("energy", "delta_energy", "delta_epsilon", "ncg", "rms", "rms_c")


def _to_float(tok: str) -> Optional[float]:
    if not _NUM_RE.match(tok):
        return None
    try:
        return float(tok.replace("D", "E").replace("d", "e"))
    except ValueError:
        return None


def _to_int(tok: str) -> Optional[int]:
    if not _INT_RE.match(tok):
        return None
    try:
        return int(tok)
    except ValueError:
        return None


def _parse_elec_fields(tokens: list[str], lineno: int,
                       warnings: list[str]) -> dict[str, Any]:
    """按列位置解析电子行尾部：缺尾列仅对应列为 None，不发生字段错位。"""
    out: dict[str, Any] = {}
    for i, name in enumerate(_ELEC_COLUMNS):
        if i >= len(tokens):
            out[name] = None
            continue
        conv = _to_int if name == "ncg" else _to_float
        val = conv(tokens[i])
        if val is None:
            # warning 只含行号与字段名，不含原始行内容或路径。
            warnings.append(f"line {lineno}: field {name} unparseable")
        out[name] = val
    return out


def _parse_ionic_fields(line: str) -> dict[str, Any]:
    """从离子步汇总行提取 F=、E0=、d E=、dns=、mag= 等 token。"""
    out: dict[str, Any] = {}
    for m in _TOKEN_RE.finditer(line):
        key = m.group(1).strip().replace(" ", "_")
        key_norm = "d" if key == "d" else key
        out[key_norm] = _to_float(m.group(2))
    return out


def parse_oszicar(text: str) -> OszicarData:
    """解析 OSZICAR：区分离子步汇总（F=/E0=）与真实电子迭代（DAV/RMM/CG 等）。

    缓冲式电子块状态机：电子行先进入 pending 缓冲，遇到 `N F= ...` 汇总行时
    将整块 ionic_step 统一确定为该汇总行的实际离子步号 N 后 flush；文件结尾
    残留的无汇总电子块按前序汇总推断编号（或暂归 1）并记录 warning，绝不声称
    为原始文件中的确定性离子步编号。“最近一次电子块”状态（energy 序列与
    最后电子步号）在每次 flush 时显式更新，不按 ionic_step 值全局筛选（重启
    片段可能重复使用相同编号）。fail soft：不完整/截断/星号溢出值不崩溃、
    不伪造数值，缺失字段置 None 并记录 parser warning。
    """
    data = OszicarData()
    ionic: list[dict[str, Any]] = []
    electronic: list[ElectronicStep] = []
    warnings: list[str] = []
    pending: list[dict[str, Any]] = []
    last_block_energies: list[float] = []
    last_elec_step = 0
    last_summary: Optional[int] = None
    last_tail = ""

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        # skip the OSZICAR header block (NION/NELEC/NKPTS/NFREE)
        if line.upper().startswith(("NION", "NELEC", "NKPTS", "NFREE")):
            continue
        m = _ELEC_RE.match(line)
        if m:
            fields = _parse_elec_fields(m.group(3).split(), lineno, warnings)
            pending.append({
                "algorithm": m.group(1),
                "electronic_step": int(m.group(2)),
                "source_line": lineno,
                **fields,
            })
            continue
        m = _IONIC_RE.match(line)
        if m:
            step = int(m.group(1))
            # 汇总行结束当前电子块：pending 行的 ionic_step 取汇总行的实际编号。
            for rec in pending:
                electronic.append(ElectronicStep(ionic_step=step, **rec))
            # 显式维护“最近一次电子块”状态：不按 ionic_step 值全局筛选，
            # 避免重启片段复用相同编号时把独立块合并。
            if pending:
                last_block_energies = [
                    r["energy"] for r in pending if r["energy"] is not None]
                last_elec_step = pending[-1]["electronic_step"]
            else:
                # 本汇总行前无电子行：最后电子块证据清空，
                # 不得继续沿用更早离子步的电子数据。
                last_block_energies = []
                last_elec_step = 0
            pending.clear()
            last_summary = step
            fields = _parse_ionic_fields(line)
            fields["step"] = step
            ionic.append(fields)
            last_tail = line
            continue
        m = _UNKNOWN_TAG_RE.match(line)
        if m:
            warnings.append(
                f"line {lineno}: unknown algorithm tag {m.group(1)} ignored")

    # 尾部电子块无 F= 汇总：编号为推断值，必须显式 warning。
    if pending:
        if last_summary is not None:
            inferred = last_summary + 1
            warnings.append(
                f"trailing electronic block assigned ionic_step={inferred} "
                "inferred from preceding ionic summary")
        else:
            inferred = 1
            warnings.append(
                "no ionic summary (F=) line found; electronic block "
                "ionic_step=1 is a local inferred index")
        for rec in pending:
            electronic.append(ElectronicStep(ionic_step=inferred, **rec))
        # 尾部 pending 作为最后电子块。
        last_block_energies = [
            r["energy"] for r in pending if r["energy"] is not None]
        last_elec_step = pending[-1]["electronic_step"]

    data.ionic_steps = ionic
    data.electronic_steps = electronic
    data.last_ionic_step = max((s["step"] for s in ionic), default=0)
    data.last_step = data.last_ionic_step  # deprecated 兼容别名
    data.last_electronic_step = last_elec_step
    data.total_electronic_lines = len(electronic)
    data.parser_warnings = warnings
    data.converged = "reached required accuracy" in last_tail
    # 离子步能量序列（deprecated 兼容字段）：显式判空，0.0 是合法 F 值不得回退 E0。
    energy_series: list[float] = []
    for s in ionic:
        f = s.get("F")
        if f is None:
            f = s.get("E0")
        if f is not None:
            energy_series.append(float(f))
    data.energy_series = energy_series
    # SCF 分析序列：最后电子块状态在 flush 时显式维护，
    # 不按 ionic_step 值对全量 electronic_steps 做全局筛选。
    data.electronic_energy_series = last_block_energies
    return data
