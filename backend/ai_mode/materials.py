"""Narrow MP reader for the agent: fixed HTTPS endpoint, no arbitrary downloads."""
from __future__ import annotations

import json
import math
import re

import httpx
from pymatgen.core import Composition, Element, Lattice, Structure
from pymatgen.io.vasp.inputs import Poscar

SUMMARY_URL = "https://api.materialsproject.org/materials/summary/"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_POSCAR_BYTES = 256 * 1024
_open_stream = httpx.stream


class MaterialsError(ValueError):
    """Safe user-facing error; never includes upstream text or credentials."""


def material_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:mp|mvc)-[0-9]{1,12}", value):
        raise MaterialsError("[MP_INVALID_ID] 需要明确的 MP 材料 ID，例如 mp-149")
    return value


def _request(key: str, params: dict) -> list[dict]:
    if not key:
        raise MaterialsError("[MP_NOT_CONFIGURED] 请在智能设置中保存 MP API key；不要把 key 发到聊天中")
    try:
        with _open_stream("GET", SUMMARY_URL,
                          headers={"X-API-KEY": key, "Accept": "application/json"},
                          params=params, timeout=20.0, follow_redirects=False) as response:
            if response.status_code in (401, 403):
                raise MaterialsError("[MP_AUTH_FAILED] MP API key 被拒绝，请在智能设置中检查")
            if response.status_code != 200:
                raise MaterialsError(f"[MP_HTTP_ERROR] MP 返回 HTTP {response.status_code}，未导入结构")
            raw = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise MaterialsError("[MP_RESPONSE_TOO_LARGE] MP 响应超出安全大小限制")
    except httpx.HTTPError:
        raise MaterialsError("[MP_NETWORK_ERROR] MP 网络请求失败，请稍后重试") from None
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise MaterialsError("[MP_INVALID_RESPONSE] MP 未返回有效 JSON") from None
    docs = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(docs, list) or not all(isinstance(doc, dict) for doc in docs):
        raise MaterialsError("[MP_INVALID_RESPONSE] MP 响应缺少材料列表")
    return docs


def search(key: str, formula: object, limit: object = 5) -> list[dict]:
    if (not isinstance(formula, str) or len(formula) > 80
            or not re.fullmatch(r"[A-Za-z0-9().]+", formula)):
        raise MaterialsError("[MP_INVALID_QUERY] 请用化学式搜索，例如 BaTiO3；不接受 URL 或命令")
    try:
        composition = Composition(formula, strict=True)
        if not composition.valid or composition.num_atoms <= 0:
            raise ValueError()
    except ValueError:
        raise MaterialsError("[MP_INVALID_QUERY] 化学式无效") from None
    if type(limit) is not int or not 1 <= limit <= 10:
        raise MaterialsError("[MP_INVALID_QUERY] limit 必须是 1 到 10 的整数")
    docs = _request(key, {"formula": formula, "_limit": limit,
                          "_fields": "material_id,formula_pretty,symmetry,energy_above_hull,band_gap"})
    rows = []
    for doc in docs[:limit]:
        mid = material_id(doc.get("material_id"))
        symmetry = doc.get("symmetry") or {}
        row = {"material_id": mid, "formula": formula}
        for name in ("band_gap", "energy_above_hull"):
            value = doc.get(name)
            row[name] = value if type(value) in (int, float) and math.isfinite(value) else None
        number = symmetry.get("number") if isinstance(symmetry, dict) else None
        row["spacegroup_number"] = number if type(number) is int and 1 <= number <= 230 else None
        rows.append(row)
    return rows


def _finite_vector(values: object) -> list[float]:
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError()
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError()
    return [float(value) for value in values]


def fetch_poscar(key: str, selected_id: object) -> dict:
    mid = material_id(selected_id)
    docs = _request(key, {"material_ids": mid, "_limit": 1,
                          "_fields": "material_id,structure"})
    if not docs:
        raise MaterialsError("[MP_NOT_FOUND] 未找到所选材料")
    if len(docs) != 1 or docs[0].get("material_id") != mid:
        raise MaterialsError("[MP_INVALID_RESPONSE] MP 返回材料 ID 与所选 ID 不一致")
    try:
        source = docs[0]["structure"]
        matrix = source["lattice"]["matrix"]
        if not isinstance(matrix, list) or len(matrix) != 3:
            raise ValueError()
        lattice = Lattice([_finite_vector(row) for row in matrix])
        if not math.isfinite(lattice.volume) or lattice.volume <= 1e-8:
            raise ValueError()
        sites = source["sites"]
        if not isinstance(sites, list) or not 1 <= len(sites) <= 500:
            raise ValueError()
        groups: dict[str, list[list[float]]] = {}
        for site in sites:
            species = site["species"]
            if not isinstance(species, list) or len(species) != 1:
                raise ValueError()
            occupancy = species[0].get("occu", 1)
            if (type(occupancy) not in (int, float) or not math.isfinite(occupancy)
                    or abs(occupancy - 1) > 1e-8):
                raise ValueError()
            element = Element(species[0]["element"]).symbol
            groups.setdefault(element, []).append(_finite_vector(site["abc"]))
        elements = [element for element, coords in groups.items() for _ in coords]
        coords = [coord for values in groups.values() for coord in values]
        structure = Structure(lattice, elements, coords, coords_are_cartesian=False)
        text = Poscar(structure, comment=f"Materials Project {mid}").get_str(significant_figures=12)
        if len(text.encode("utf-8")) > MAX_POSCAR_BYTES:
            raise ValueError()
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        raise MaterialsError("[MP_INVALID_STRUCTURE] 结构无效、含无序/部分占位、非有限数值或超过 500 原子；未生成 POSCAR") from None
    return {"material_id": mid, "formula": structure.composition.reduced_formula,
            "atom_count": len(structure), "content": text, "source_url": SUMMARY_URL}
