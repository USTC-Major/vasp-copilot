"""Materials Project REST API client (v2) — read-only search + structure retrieval.

API: api.materialsproject.org/v2 (OpenAPI-discovered)
  GET /materials/summary/   - summary search (query params) incl. embedded structure
  POST /materials/core/find_structure/ - structure-doc search (unused for MVP)
Auth: X-API-KEY header. All calls read-only.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import httpx

from ..core.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)


def _compact(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an MP v2 summary doc into the compact UI field set."""
    lattice = (doc.get("structure") or {}).get("lattice") or doc.get("lattice") or {}
    sym = doc.get("symmetry") or doc.get("spacegroup") or {}
    return {
        "material_id": doc.get("material_id") or doc.get("task_id") or "",
        "formula": doc.get("formula_pretty") or doc.get("pretty_formula") or "",
        "elements": list(doc.get("elements") or []),
        "n_elements": doc.get("nelements") or len(doc.get("elements") or []),
        "spacegroup": {
            "symbol": sym.get("symbol"),
            "number": sym.get("number"),
            "point_group": sym.get("point_group") or sym.get("point_group_symmetry"),
            "crystal_system": sym.get("crystal_system"),
        },
        "lattice": {
            "a": lattice.get("a"),
            "b": lattice.get("b"),
            "c": lattice.get("c"),
            "alpha": lattice.get("alpha"),
            "beta": lattice.get("beta"),
            "gamma": lattice.get("gamma"),
            "volume": lattice.get("volume") or doc.get("volume"),
        },
        "density": doc.get("density"),
        "band_gap": doc.get("band_gap"),
        "is_metal": doc.get("is_metal"),
        "is_stable": doc.get("is_stable"),
        "formation_energy_per_atom": doc.get("formation_energy_per_atom"),
        "energy_above_hull": doc.get("energy_above_hull"),
        "total_magnetization": doc.get("total_magnetization"),
        "ordering": doc.get("ordering"),
    }


class MaterialsProjectClient:
    """Thin read-only client for the Materials Project API v2."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.materialsproject.org",
        timeout_seconds: float = 40.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key or ""
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client if client is not None else httpx.Client(
            timeout=timeout_seconds, follow_redirects=True)

    def _request(self, method: str, path: str, params=None,
                 json=None) -> Dict[str, Any]:
        url = self._base_url + path
        headers = {"X-API-KEY": self._api_key, "Accept": "application/json"}
        resp = self._client.request(
            method, url, headers=headers, params=params, json=json,
            timeout=self._timeout)
        if resp.status_code in (401, 403):
            raise ConflictError(
                "MP_AUTH_FAILED",
                "Materials Project API key rejected (401/403). "
                "请在材料库设置中更换有效 API key。",
            )
        if resp.status_code == 404:
            raise NotFoundError("MP_NOT_FOUND",
                                "Materials Project resource not found")
        if resp.status_code >= 400:
            raise ValidationError(
                "MP_API_ERROR",
                f"Materials Project API error {resp.status_code}: "
                f"{resp.text[:200]}",
            )
        try:
            return resp.json() if resp.content else {}
        except ValueError:
            return {}

    # -- public API -----------------------------------------------------
    def search(self, criteria: Dict[str, Any],
               limit: int = 20) -> List[Dict[str, Any]]:
        """GET /materials/summary/ using criteria-derived query params."""
        params = _criteria_to_params(criteria, limit)
        data = self._request("GET", "/materials/summary/", params=params)
        docs = data.get("data") if isinstance(data, dict) else None
        if not isinstance(docs, list):
            raise ValidationError("MP_INVALID_RESPONSE",
                                  "Materials Project returned no search list")
        return [_compact(d) for d in docs]

    def get_structure_doc(self, material_id: str) -> Dict[str, Any]:
        """Fetch one summary doc with embedded pymatgen structure."""
        data = self._request(
            "GET", "/materials/summary/",
            params={"material_ids": material_id,
                    "_fields": "material_id,formula_pretty,structure"})
        docs = (data or {}).get("data") or []
        if not docs:
            raise NotFoundError("MP_NOT_FOUND",
                                f"material {material_id} not found")
        return docs[0]

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass


_SEARCH_FIELDS_STR = (
    "material_id,formula_pretty,elements,nelements,symmetry,structure,"
    "density,volume,band_gap,is_metal,is_stable,formation_energy_per_atom,"
    "energy_above_hull,total_magnetization,ordering"
)


def _criteria_to_params(criteria: Dict[str, Any], limit: int) -> Dict[str, Any]:
    """Map a criteria dict to /materials/summary/ query params."""
    params: Dict[str, Any] = {"_limit": max(1, min(int(limit), 50))}
    params["_fields"] = _SEARCH_FIELDS_STR
    if isinstance(criteria, dict):
        formula = criteria.get("formula")
        if formula:
            params["formula"] = formula
        elements = criteria.get("elements")
        if isinstance(elements, list) and elements:
            params["elements"] = ",".join(elements)
        chemsys = criteria.get("chemsys")
        if chemsys:
            params["chemsys"] = chemsys
        if criteria.get("is_stable") is not None:
            params["is_stable"] = str(bool(criteria["is_stable"])).lower()
        if criteria.get("is_metal") is not None:
            params["is_metal"] = str(bool(criteria["is_metal"])).lower()
        ordering = criteria.get("ordering")
        if ordering:
            params["ordering"] = ordering
        for key, sign in (("band_gap", "band_gap"),):
            pass
        bg = criteria.get("band_gap")
        if isinstance(bg, dict):
            if "$gte" in bg:
                params["band_gap_min"] = bg["$gte"]
            if "$lte" in bg:
                params["band_gap_max"] = bg["$lte"]
        eah = criteria.get("energy_above_hull")
        if isinstance(eah, dict) and "$lte" in eah:
            params["energy_above_hull"] = str(eah["$lte"])
    return params


_ELEMENT_RE = re.compile(r"[A-Z][a-z]?")
_KNOWN_ELEMENTS = set([
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni",
    "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd",
    "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd",
    "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Th", "U"])


def _extract_elements(text: str) -> List[str]:
    found: List[str] = []
    for m in _ELEMENT_RE.finditer(text):
        tok = m.group(0)
        base = tok[0].upper() + tok[1:].lower()
        if base in _KNOWN_ELEMENTS and base not in found:
            found.append(base)
    return found


def parse_requirement(requirement: str) -> Dict[str, Any]:
    """Deterministic no-LLM fallback: turn free text into MP query params."""
    text = requirement.strip()
    if not text:
        return {}
    criteria: Dict[str, Any] = {}
    formula = extract_formula(text)
    if formula:
        criteria["formula"] = formula
    else:
        els = _extract_elements(_strip_units(text))
        if els:
            criteria["elements"] = els
    if re.search(r"禁带|带隙|ev|band.?gap|bandgap|^\s*带", text, re.I):
        bg: Dict[str, Any] = {}
        mrange = re.search(r"(\d+(?:\.\d+)?)\s*[-到~～]\s*(\d+(?:\.\d+)?)", text)
        if mrange:
            bg["$gte"] = float(mrange.group(1))
            bg["$lte"] = float(mrange.group(2))
        else:
            mval = re.search(r"(\d+(?:\.\d+)?)", text)
            if mval:
                bg["$gte"] = float(mval.group(1))
            else:
                bg["$gte"] = 0.0
        criteria["band_gap"] = bg
    if criteria:
        return criteria
    raise ValidationError(
        "MP_QUERY_TOO_VAGUE",
        "请至少给出元素/化学式描述，例如「Fe2O3」或「带隙>1eV 的氧化物」")


def _upper_uniq(tokens: List[str]) -> List[str]:
    out: List[str] = []
    for t in tokens:
        v = t[0].upper() + t[1:].lower()
        if v not in out:
            out.append(v)
    return out


def _to_mp_formula(run: List[Any]) -> Optional[str]:
    parts: List[str] = []
    uniq: List[str] = []
    for m in run:
        seg = m.group(0)
        em = re.match(r"([A-Z][a-z]?)([0-9]*)", seg)
        el = em.group(1)
        if el not in _KNOWN_ELEMENTS:
            return None
        cnt = int(em.group(2)) if em.group(2) else 1
        if el not in uniq:
            uniq.append(el)
        parts.append(el if cnt == 1 else (el + str(cnt)))
    if len(run) < 2 or len(uniq) < 2:
        return None
    return " ".join(parts)


def extract_formula(text: str) -> Optional[str]:
    """Return an MP formula (e.g. 'Mn O2') if text holds a contiguous
    formula-like element run; else None to fall back to element search."""
    spans = list(re.finditer(r"[A-Z][a-z]?[0-9]*", text))
    if not spans:
        return None
    runs: List[List[Any]] = []
    cur = [spans[0]]
    for s_span in spans[1:]:
        if s_span.start() - cur[-1].end() > 2:
            runs.append(cur)
            cur = [s_span]
        else:
            cur.append(s_span)
    runs.append(cur)
    for run in runs:
        mp_formula = _to_mp_formula(run)
        if mp_formula:
            return mp_formula
    return None


def _strip_units(text: str) -> str:
    """Remove common energy-units tokens (eV/meV/keV) so they are not
    mis-parsed as element symbols (e.g. 'V')."""
    return re.sub(r"\b(?:meV|keV|eV|ev|McV)\b", " ", text, flags=re.I)
