from __future__ import annotations

import re
from typing import Optional

from ..schemas.cif import CifData


_CELL_KEYS = {
    "_cell_length_a": ("lattice_a", float),
    "_cell_length_b": ("lattice_b", float),
    "_cell_length_c": ("lattice_c", float),
    "_cell_angle_alpha": ("angle_alpha", float),
    "_cell_angle_beta": ("angle_beta", float),
    "_cell_angle_gamma": ("angle_gamma", float),
}


def _unquote(tok: str) -> str:
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("'", "\""):
        return tok[1:-1]
    return tok


def _split_values(tail: str) -> list[str]:
    """将 CIF 数据行切分为 token，忽略尾部注释（'#'）。"""
    tail = re.sub(r"#[^\n]*", "", tail)
    parts = tail.strip().split()
    out = [parts[0]]
    # quoted values may span multiple tokens: rejoin until the closing quote
    i = 1
    buf = ""
    for j in range(1, len(parts)):
        tok = parts[j]
        if buf:
            buf += " " + tok
            if tok.endswith("\"") or tok.endswith("\'"):
                out.append(buf)
                buf = ""
            continue
        if (tok.startswith("\"") or tok.startswith("\'")) and not (tok.endswith("\"") or tok.endswith("\'")):
            buf = tok
            continue
        out.append(tok)
    if buf:
        out.append(buf)
    return out


def _looks_atom_site_header(line: str) -> bool:
    return ("._atom_site_" in line or "_atom_site_" in line) and \
           ("_atom_site_type_symbol" in line or "_atom_site_label" in line)


def parse_cif(text: str, source_file: str = "") -> CifData:
    """解析 CIF 得到元素/计数 + 晶胞（手写实现，仅供诊断元数据）。

    本函数不保留原子分数坐标，不得作为生成 POSCAR/结构的依据；
    CIF -> 真实 Structure/POSCAR 的转换见 services.cif_converter。
    """
    data = CifData(source_file=source_file)
    lines = text.splitlines()
    i = 0
    n = len(lines)

    for line in lines:
        if line.lstrip().startswith("data_"):
            data.formula = line.strip()[5:].strip()

    # 1) cell parameters (key on the left, value on the right)
    for line in lines:
        st = line.strip()
        if not st or st.startswith("#"):
            continue
        eq = st.split()
        key = eq[0].lower() if eq else ""
        if key in _CELL_KEYS and len(eq) >= 2:
            attr, cast = _CELL_KEYS[key]
            try:
                setattr(data, attr, cast(_unquote(eq[1])))
            except (ValueError, TypeError):
                pass

    # 2) space group
    for line in lines:
        st = line.strip()
        m = re.match(r"_symmetry_space_group_name_H-M\s+([^#]+)", st)
        if m:
            data.space_group = _unquote(m.group(1).strip())
            break

    # 3) atom site loop
    col_idx = None
    while i < n:
        st = lines[i].strip()
        if st.startswith("loop_"):
            cols: list[str] = []
            j = i + 1
            while j < n:
                c = lines[j].strip()
                if not c or c.startswith("#"):
                    j += 1
                    continue
                if c.startswith("_") or "_atom_site_" in c:
                    cols.append(c.split()[0])
                    j += 1
                    continue
                break  # first data row reached
            if any("_atom_site_" in c for c in cols):
                col_idx = j  # start of data rows
                idx = {}
                for k, c in enumerate(cols):
                    low = c.lower()
                    if low.endswith("_type_symbol") or low.endswith("_label"):
                        idx.setdefault("sym", k)
                        idx.setdefault("label", k)
                    if low.endswith("_fract_x"):
                        idx["x"] = k
                    if low.endswith("_fract_y"):
                        idx["y"] = k
                    if low.endswith("_fract_z"):
                        idx["z"] = k
                i = col_idx
                break
        i += 1

    if col_idx is not None and "sym" in idx:
        sym_k = idx["sym"]
        seen: list[str] = []
        counts: list[int] = []
        for j in range(col_idx, n):
            st = lines[j].strip()
            if not st or st.startswith("#") or st.startswith("loop_") or st.startswith("_"):
                continue
            toks = _split_values(st)
            if len(toks) <= sym_k:
                continue
            sym = re.sub(r"[0-9]+", "", _unquote(toks[sym_k])).strip()
            if not sym or sym.startswith("#"):
                continue
            if sym in seen:
                counts[seen.index(sym)] += 1
            else:
                seen.append(sym)
                counts.append(1)
        data.elements = seen
        data.counts = counts

    return data
