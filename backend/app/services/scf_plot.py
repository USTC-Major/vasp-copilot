"""SCF display evidence, independent of diagnostic rule decisions."""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any

from ..schemas.parsed import ParsedRunData


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = float(value)
        return converted if math.isfinite(converted) else None
    except (OverflowError, ValueError):
        return None


def _status(value: float | None, raw: str | None) -> str:
    if value is not None:
        return "zero" if value == 0 else "available"
    if raw is None:
        return "missing"
    try:
        if not math.isfinite(float(raw.replace("D", "E").replace("d", "e"))):
            return "non_finite"
    except ValueError:
        pass
    return "unparseable"


def _resolution(raw: str | None) -> float | None:
    """Last printed decimal place, not a claim about physical accuracy."""
    if raw is None:
        return None
    try:
        number = Decimal(raw.replace("D", "E").replace("d", "e"))
        if not number.is_finite():
            return None
        value = float(Decimal(1).scaleb(number.as_tuple().exponent))
        return value if math.isfinite(value) and value > 0 else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _parameter(parsed: ParsedRunData, name: str) -> dict:
    candidates = [dict(p) for p in parsed.outcar.scf_parameters if p.get("name") == name]
    if name in parsed.incar.effective:
        assignment = next((a for a in reversed(parsed.incar.assignments) if a.name == name), None)
        candidates.append({"name": name, "value": _finite(parsed.incar.effective[name]),
                           "source": "INCAR", "source_line": assignment.source_line if assignment else None,
                           "raw": assignment.raw_value if assignment else None})
    for candidate in candidates:
        value = _finite(candidate.get("value"))
        valid = value is not None and (value >= 0 if name == "EDIFF" else value > 0 and value.is_integer())
        candidate["value"] = int(value) if valid and name == "NELM" else value if valid else None
        candidate["valid"] = valid
    output = [c for c in candidates if c["source"] == "OUTCAR"]
    # A concatenated output with inconsistent echoes cannot be assigned safely.
    conflict = len({c["value"] for c in output if c["valid"]}) > 1
    chosen = None
    if not conflict:
        # Invalid output evidence blocks fallback to an apparently usable input.
        chosen = output[-1] if output else candidates[-1] if candidates else None
    value = chosen["value"] if chosen else None
    input_conflict = bool(chosen and chosen["source"] == "OUTCAR" and any(
        c["source"] == "INCAR" and c["valid"] and c["value"] != value for c in candidates))
    return {
        "value": value, "source": chosen["source"] if chosen else None,
        "source_line": chosen["source_line"] if chosen else None,
        "raw": chosen["raw"] if chosen else None,
        "status": "conflict" if conflict else "available" if value is not None else "invalid" if candidates else "missing",
        "input_conflict": input_conflict, "candidates": candidates,
        "scope": "run_unassigned" if output else "input_unverified",
    }


def build_scf_plot(parsed: ParsedRunData) -> dict:
    series = []
    previous = None
    legacy_block = 1
    for step in parsed.oszicar.electronic_steps:
        # Older parsed snapshots lack block IDs. Infer only contiguous groups;
        # repeated/nonmonotonic electronic indices start a separate group.
        if previous and (step.ionic_step != previous.ionic_step or step.electronic_step <= previous.electronic_step):
            legacy_block += 1
        block = step.block_id if step.block_id is not None else legacy_block
        energy = _finite(step.energy)
        delta = _finite(step.delta_energy)
        epsilon = _finite(step.delta_epsilon)
        status = "non_finite" if step.delta_energy is not None and delta is None else _status(delta, step.delta_energy_raw)
        source = "oszicar" if step.delta_energy is not None or step.delta_energy_raw is not None else None
        derivation = None
        if (step.delta_energy is None and step.delta_energy_raw is None and previous is not None
                and series[-1]["block_id"] == block
                and step.electronic_step == previous.electronic_step + 1
                and energy is not None and _finite(previous.energy) is not None):
            difference = energy - float(previous.energy)
            if step.energy_raw is not None and previous.energy_raw is not None:
                # Decimal subtraction reflects the printed E tokens exactly;
                # binary float noise must not move a one-quantum difference
                # beyond the printed-resolution boundary.
                try:
                    printed_difference = (Decimal(step.energy_raw.replace("D", "E").replace("d", "e"))
                                          - Decimal(previous.energy_raw.replace("D", "E").replace("d", "e")))
                    difference = float(printed_difference)
                except (InvalidOperation, ValueError, OverflowError):
                    pass
            if math.isfinite(difference):
                delta = difference
                source = "derived"
                current_resolution, previous_resolution = _resolution(step.energy_raw), _resolution(previous.energy_raw)
                resolution = ((current_resolution + previous_resolution) / 2
                              if current_resolution is not None and previous_resolution is not None else None)
                status = ("precision_unknown" if resolution is None else
                          "precision_limited" if abs(delta) <= resolution else "available")
                derivation = {"previous_source_line": previous.source_line,
                              "previous_energy_ev": float(previous.energy),
                              "previous_energy_raw": previous.energy_raw,
                              "resolution_ev": resolution}
        series.append({
            "ionic_step": step.ionic_step, "electronic_step": step.electronic_step,
            "block_id": block, "ionic_step_inferred": step.ionic_step_inferred,
            "energy_ev": energy, "energy_status": "non_finite" if step.energy is not None and energy is None else _status(energy, step.energy_raw), "energy_raw": step.energy_raw,
            "delta_energy_ev": delta, "delta_energy_status": status,
            "delta_energy_raw": step.delta_energy_raw, "delta_energy_source": source,
            "delta_energy_derivation": derivation,
            "delta_epsilon_ev": epsilon, "delta_epsilon_status": _status(epsilon, step.delta_epsilon_raw),
            "delta_epsilon_raw": step.delta_epsilon_raw,
            "source_file": "OSZICAR", "source_line": step.source_line, "algorithm": step.algorithm,
        })
        previous = step
    return {"x_label": "电子步", "y_label": "自由能 E (eV)", "series": series,
            "parameters": {"ediff": _parameter(parsed, "EDIFF"), "nelm": _parameter(parsed, "NELM")},
            "convergence_evidence": parsed.outcar.electronic_convergence_evidence}
