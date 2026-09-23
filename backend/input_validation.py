"""Bounded, mechanical VASP input checks for creation and submit prechecks.

This module deliberately does not import the diagnostic parsers, AI, SSH or API.
It does not judge physical parameter choices or the quality of a potential.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


TEXT_LIMIT = 2 * 1024 * 1024
POTCAR_LIMIT = 32 * 1024 * 1024
MAX_ATOMS = 100_000
_ELEMENTS = set("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())
_INTEGER = re.compile(r"[0-9]+\Z")
_FLOAT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?\Z")
_TAG = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class InputValidationError(ValueError):
    def __init__(self, code: str, message: str, line: int | None = None):
        self.code, self.message, self.line = code, message, line
        super().__init__(f"{message}（第 {line} 行）" if line else message)


@dataclass(frozen=True)
class PoscarInfo:
    elements: tuple[str, ...]
    counts: tuple[int, ...]
    atom_count: int
    matrix: tuple[tuple[float, float, float], ...]
    volume: float
    coordinate_mode: str
    selective_dynamics: bool
    vasp4: bool


def _fail(code: str, message: str, line: int | None = None):
    raise InputValidationError(code, message, line)


def _text(raw: bytes | str, label: str, limit: int = TEXT_LIMIT) -> str:
    if isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8")
        except UnicodeError:
            _fail("INPUT_ENCODING_INVALID", f"{label} 不是有效 UTF-8 文本")
    elif isinstance(raw, bytes):
        encoded = raw
    else:
        _fail("INPUT_TYPE_INVALID", f"{label} 输入类型无效")
    if not encoded or len(encoded) > limit:
        _fail("INPUT_SIZE_INVALID", f"{label} 为空或超过本轮 {limit} 字节上限")
    try:
        text = encoded.decode("utf-8-sig")
    except UnicodeDecodeError:
        _fail("INPUT_ENCODING_INVALID", f"{label} 不是有效 UTF-8 文本")
    if any((ord(ch) < 32 and ch not in "\r\n\t") or ord(ch) == 127 for ch in text):
        _fail("INPUT_CONTROL_INVALID", f"{label} 含非法控制字符")
    return text


def _number(token: str, label: str, line: int) -> float:
    if len(token) > 128 or not _FLOAT.fullmatch(token):
        _fail("INPUT_NUMBER_INVALID", f"{label} 数值无效或非有限", line)
    try:
        value = float(token.replace("D", "E").replace("d", "e"))
    except (ValueError, OverflowError):
        _fail("INPUT_NUMBER_INVALID", f"{label} 数值无效或非有限", line)
    if not math.isfinite(value):
        _fail("INPUT_NUMBER_INVALID", f"{label} 数值无效或非有限", line)
    return value


def _vector(tokens: list[str], label: str, line: int) -> tuple[float, float, float]:
    if len(tokens) < 3:
        _fail("INPUT_VECTOR_INCOMPLETE", f"{label} 需要三个有限数值", line)
    return tuple(_number(token, label, line) for token in tokens[:3])  # type: ignore[return-value]


def validate_poscar(raw: bytes | str) -> PoscarInfo:
    lines = _text(raw, "POSCAR").splitlines()
    if len(lines) < 8:
        _fail("POSCAR_INCOMPLETE", "POSCAR 缺少必需头部或坐标行")
    # Line 1 is the comment even if blank. Do not strip or shift it.
    scale_tokens = lines[1].split()
    if len(scale_tokens) not in (1, 3):
        _fail("POSCAR_SCALE_INVALID", "POSCAR 缩放因子需要一个或三个数值", 2)
    scales = [_number(t, "POSCAR 缩放因子", 2) for t in scale_tokens]
    if (len(scales) == 1 and scales[0] == 0) or (len(scales) == 3 and any(s <= 0 for s in scales)):
        _fail("POSCAR_SCALE_INVALID", "POSCAR 缩放因子不能为零，三个缩放因子均须为正", 2)
    rows = [_vector(lines[i].split(), "晶格矢量", i + 1) for i in range(2, 5)]
    a, b, c = rows
    determinant = (a[0] * (b[1] * c[2] - b[2] * c[1])
                   - a[1] * (b[0] * c[2] - b[2] * c[0])
                   + a[2] * (b[0] * c[1] - b[1] * c[0]))
    norms = [math.hypot(*row) for row in rows]
    if (not math.isfinite(determinant) or not all(math.isfinite(n) for n in norms)
            or min(norms) <= 0 or abs(determinant) <= 1e-10 * math.prod(norms)):
        _fail("POSCAR_LATTICE_DEGENERATE", "POSCAR 晶胞退化或体积无效")
    if len(scales) == 1:
        factor = scales[0] if scales[0] > 0 else (abs(scales[0]) / abs(determinant)) ** (1 / 3)
        matrix = tuple(tuple(v * factor for v in row) for row in rows)
    else:
        matrix = tuple(tuple(row[j] * scales[j] for j in range(3)) for row in rows)
    if any(not math.isfinite(v) for row in matrix for v in row):
        _fail("POSCAR_LATTICE_DEGENERATE", "POSCAR 缩放后晶胞数值无效")
    scaled_norms = [math.hypot(*row) for row in matrix]
    if (not all(math.isfinite(n) and n > 0 for n in scaled_norms)
            or not math.isfinite(math.prod(scaled_norms))):
        _fail("POSCAR_LATTICE_DEGENERATE", "POSCAR 缩放后晶胞数值无效")
    scaled_volume = abs(determinant) * (math.prod((factor, factor, factor)) if len(scales) == 1 else math.prod(scales))
    if not math.isfinite(scaled_volume) or scaled_volume <= 0:
        _fail("POSCAR_LATTICE_DEGENERATE", "POSCAR 缩放后晶胞体积无效")
    species_or_counts = lines[5].split()
    if not species_or_counts:
        _fail("POSCAR_SPECIES_INVALID", "POSCAR 缺少物种或原子数量", 6)
    vasp4 = all(_INTEGER.fullmatch(t) for t in species_or_counts)
    if vasp4:
        elements: tuple[str, ...] = ()
        count_line = 5
    else:
        if any(t not in _ELEMENTS for t in species_or_counts):
            _fail("POSCAR_SPECIES_UNSUPPORTED", "POSCAR 物种标签不受本轮支持；请使用元素符号", 6)
        elements = tuple(species_or_counts)
        count_line = 6
    if count_line >= len(lines):
        _fail("POSCAR_COUNTS_INVALID", "POSCAR 缺少原子数量行")
    count_tokens = lines[count_line].split()
    if not count_tokens or any(len(t) > 12 or not _INTEGER.fullmatch(t) for t in count_tokens):
        _fail("POSCAR_COUNTS_INVALID", "POSCAR 原子数量需要非负整数", count_line + 1)
    counts = tuple(int(t) for t in count_tokens)
    total = sum(counts)
    if (not total or total > MAX_ATOMS or (elements and len(elements) != len(counts))):
        _fail("POSCAR_COUNTS_INVALID", "POSCAR 物种数量不匹配或总原子数无效", count_line + 1)
    mode_line = count_line + 1
    selective = False
    if mode_line < len(lines) and lines[mode_line].strip().lower().startswith("s"):
        selective = True
        mode_line += 1
    if mode_line >= len(lines) or not lines[mode_line].strip():
        _fail("POSCAR_MODE_MISSING", "POSCAR 缺少坐标模式行", mode_line + 1)
    mode_first = lines[mode_line].strip()[0].lower()
    mode = "cartesian" if mode_first in ("c", "k") else "direct"
    start = mode_line + 1
    if len(lines) < start + total:
        _fail("POSCAR_COORDINATES_MISSING", "POSCAR 坐标行少于声明的原子数")
    for i in range(start, start + total):
        tokens = lines[i].split()
        _vector(tokens, "原子坐标", i + 1)
        if selective and (len(tokens) < 6 or any(t.lower() not in ("t", "f") for t in tokens[3:6])):
            _fail("POSCAR_FLAGS_INVALID", "选择性动力学坐标需要三个 T/F 标志", i + 1)
    return PoscarInfo(elements, counts, total, matrix, scaled_volume, mode, selective, vasp4)


def validate_incar(raw: bytes | str) -> None:
    text = _text(raw, "INCAR")
    count = 0
    pending = ""
    for line_no, line in enumerate(text.splitlines(), 1):
        content = line.split("#", 1)[0].split("!", 1)[0].strip()
        if not content:
            continue
        pending += " " + content
        if pending.rstrip().endswith("\\"):
            pending = pending.rstrip()[:-1]
            continue
        for part in pending.split(";"):
            part = part.strip()
            if not part:
                continue
            key, sep, value = part.partition("=")
            if not sep or not _TAG.fullmatch(key.strip()) or not value.strip():
                _fail("INCAR_FORMAT_UNSUPPORTED", "INCAR 包含无法识别或空值的赋值", line_no)
            count += 1
        pending = ""
    if pending or not count:
        _fail("INCAR_FORMAT_UNSUPPORTED", "INCAR 缺少完整的非空赋值")


def validate_kpoints(raw: bytes | str) -> None:
    text = _text(raw, "KPOINTS")
    lines = [line.strip() for line in text.splitlines()]
    if len(lines) < 4:
        _fail("KPOINTS_INCOMPLETE", "KPOINTS 缺少必需头部或网格/点")
    head = lines[1].split()
    if not head or len(head[0]) > 12 or not _INTEGER.fullmatch(head[0]):
        _fail("KPOINTS_COUNT_INVALID", "KPOINTS 第二行需要非负整数", 2)
    count = int(head[0])
    kind = lines[2].strip().lower()
    if count == 0:
        if not kind or kind[0] not in ("g", "m"):
            _fail("KPOINTS_FORMAT_UNSUPPORTED", "KPOINTS 本轮仅支持 Gamma/Monkhorst 自动网格等已识别格式", 3)
        grid = lines[3].split()
        if len(grid) < 3 or any(len(t) > 12 or not _INTEGER.fullmatch(t) or int(t) < 1 for t in grid[:3]):
            _fail("KPOINTS_GRID_INVALID", "KPOINTS 网格需要三个正整数", 4)
        if len(lines) >= 5 and lines[4]:
            _vector(lines[4].split(), "KPOINTS 位移", 5)
        if any(line and not line.startswith(("!", "#")) for line in lines[5:]):
            _fail("KPOINTS_FORMAT_UNSUPPORTED", "KPOINTS 自动网格后包含本轮未验证的内容")
        return
    if kind.startswith("l"):
        if len(lines) < 6 or lines[3].lower()[:1] not in ("r", "c", "k", "f"):
            _fail("KPOINTS_INCOMPLETE", "Line-mode KPOINTS 缺少坐标模式或端点")
        points = [(i, line) for i, line in enumerate(lines[4:], 5) if line]
        if len(points) < 2 or len(points) % 2:
            _fail("KPOINTS_INCOMPLETE", "Line-mode KPOINTS 端点必须成对")
        for number, line in points:
            _vector(line.split("!", 1)[0].split(), "KPOINTS 端点", number)
        return
    if kind[:1] not in ("r", "c", "k", "f"):
        _fail("KPOINTS_FORMAT_UNSUPPORTED", "KPOINTS 坐标模式不受本轮预检支持", 3)
    points = [(i, line) for i, line in enumerate(lines[3:], 4) if line]
    if len(points) < count:
        _fail("KPOINTS_INCOMPLETE", "KPOINTS 显式点少于声明数量")
    for number, line in points[:count]:
        tokens = line.split("!", 1)[0].split()
        if len(tokens) < 4:
            _fail("KPOINTS_INCOMPLETE", "KPOINTS 显式点需要坐标和权重", number)
        _vector(tokens, "KPOINTS 显式点", number)
        _number(tokens[3], "KPOINTS 权重", number)
    if any(not line.startswith(("!", "#")) for _number, line in points[count:]):
        _fail("KPOINTS_FORMAT_UNSUPPORTED", "KPOINTS 显式点后包含本轮未验证的尾段")


def validate_potcar(raw: bytes, elements: tuple[str, ...] | None) -> tuple[str, ...]:
    text = _text(raw, "POTCAR", POTCAR_LIMIT)
    blocks = re.split(r"(?im)^\s*End of Dataset\s*$", text)
    if len(blocks) < 2 or blocks[-1].strip():
        _fail("POTCAR_METADATA_UNVERIFIED", "POTCAR 数据集结束标志无法完整识别")
    found: list[str] = []
    for block in blocks[:-1]:
        vr = re.findall(r"(?im)^\s*VRHFIN\s*=\s*([A-Z][a-z]?)\s*:", block)
        title = re.findall(r"(?im)^\s*TITEL\s*=\s*\S+\s+([A-Z][a-z]?(?:_[A-Za-z0-9]+)?)\b", block)
        if len(vr) != 1 or len(title) != 1 or vr[0] not in _ELEMENTS or title[0].split("_")[0] != vr[0]:
            _fail("POTCAR_METADATA_UNVERIFIED", "POTCAR 物种元数据无法可靠识别")
        found.append(vr[0])
    if elements is not None and tuple(found) != elements:
        _fail("POTCAR_SPECIES_MISMATCH", "POTCAR 数据集数量或物种顺序与 POSCAR 不一致")
    return tuple(found)
