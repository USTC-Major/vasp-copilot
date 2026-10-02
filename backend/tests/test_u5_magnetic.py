"""Controlled magnetic evidence fixtures; no external calculations/services."""
from __future__ import annotations

import json
import pytest

from app.api.v1.diagnosis import _plots_compat
from app.diagnostics.magnetic_analysis import build_magnetic_analysis
from app.diagnostics.rules.magnetic import LocalMomentCollapseRule, MagmomSignFlipRule
from app.parsers.incar import parse_incar
from app.parsers.magnetic_evidence import parse_oszicar_magnetic
from app.parsers.outcar import parse_outcar
from app.parsers.poscar import parse_poscar
from app.schemas.parsed import IncarData, OutcarData, ParsedRunData
from app.services.diagnosis_service import _build_plots, _load_parsed


def table(values, *, ids=None, f=False, closed=True, axis="x"):
    columns = ["s", "p", "d"] + (["f"] if f else []) + ["tot"]
    result = [f" magnetization ({axis})", " # of ion " + " ".join(columns), " ----------------"]
    for ion, value in zip(ids or range(1, len(values) + 1), values):
        result.append(f" {ion} " + " ".join(["0"] * (len(columns) - 1) + [str(value)]))
    if closed:
        result += [" ----------------", " tot " + " ".join(["0"] * (len(columns) - 1) + ["9.999"])]
    return "\n".join(result) + "\n"


def poscar(n, *, positions=None, elements="Fe", counts=None, mode="Direct"):
    positions = positions or [f"{i / n:.8f} 0.00000000 0.00000000" for i in range(n)]
    return "fixture\n1\n1 0 0\n0 1 0\n0 0 1\n" + (elements + "\n" if elements else "") + (counts or str(n)) + "\n" + mode + "\n" + "\n".join(positions) + "\n"


def run(reference=(2, -2, 0), output=(1.8, -1.8, 0), *, incar="", extra="", tables=None, structure=None, normal=True):
    parsed = ParsedRunData(incar=parse_incar("MAGMOM=" + " ".join(map(str, reference)) + "\n" + incar))
    parsed.poscar = parse_poscar(structure or poscar(len(reference)))
    parsed.poscar.source_file = "POSCAR"
    parsed.outcar = parse_outcar(" ISPIN = 2\n" + extra + (tables if tables is not None else table(output))
                                 + (" General timing and accounting informations\n" if normal else ""))
    parsed.calculation_mode = parsed.outcar.calculation_mode
    return parsed


@pytest.mark.parametrize("output,pattern", [((1.8, -1.8, 0), "retained"), ((-1.8, 1.8, 0), "global_reversed"),
                                             ((-1.8, -1.8, 0), "local_changes"), ((-1.8, 1.8, .2), "local_changes"),
                                             ((-1.8, .01, 0), "local_changes")])
def test_ordered_reference_patterns_are_descriptive(output, pattern):
    parsed = run(output=output)
    analysis = build_magnetic_analysis(parsed)
    assert analysis["status"] == "ready" and analysis["pattern"] == pattern
    assert "不判断" in " ".join(analysis["summary"] + analysis["notes"])
    assert [a["atom_index"] for a in analysis["atoms"]] == [1, 2, 3]
    assert all(a["comparison_available"] for a in analysis["atoms"])
    assert len(MagmomSignFlipRule().run(parsed)) == (1 if pattern == "local_changes" and any(a["orientation"] == "reversed" for a in analysis["atoms"]) else 0)


def test_reverse_and_attenuation_coexist_without_collapsing_meanings():
    parsed = run(output=(-.8, -1.8, .2))
    atoms = build_magnetic_analysis(parsed)["atoms"]
    assert atoms[0]["orientation"] == "reversed" and atoms[0]["magnitude"] == "decreased" and atoms[0]["attenuated"]
    assert atoms[2]["magnitude"] == "emerged"
    assert LocalMomentCollapseRule().run(parsed) == []
    collapsed = run(output=(.05, -1.8, 0))
    assert len(LocalMomentCollapseRule().run(collapsed)) == 1
    assert "输入 MAGMOM 参考" in LocalMomentCollapseRule().run(collapsed)[0].summary


def test_negative_to_positive_local_reverse_uses_actual_ion_not_compressed_index():
    parsed = run(reference=(2, -2, -2), tables=table(["********", 1, -1.8]))
    atoms = build_magnetic_analysis(parsed)["atoms"]
    assert atoms[0]["output_moment"] is None and not atoms[0]["comparison_available"]
    assert atoms[1]["orientation"] == "reversed"
    issue = MagmomSignFlipRule().run(parsed)[0]
    assert "atom2" in issue.summary and "atom1" not in issue.summary


@pytest.mark.parametrize("setting,global_allowed", [("NUPDOWN=0", False), ("NUPDOWN=-1", True), ("BEXT=0", True),
                                                   ("BEXT=0.1", False), ("I_CONSTRAINED_M=1", False), ("NUPDOWN=bad", False)])
def test_fields_and_constraints_guard_whole_reversal(setting, global_allowed):
    a = build_magnetic_analysis(run(output=(-1.8, 1.8, 0), incar=setting))
    assert (a["pattern"] == "global_reversed") is global_allowed
    assert a["atoms"][0]["orientation"] == "reversed"


def test_echo_conflict_comments_and_restart_evidence():
    a = build_magnetic_analysis(run(output=(-1.8, 1.8, 0), incar="NUPDOWN=-1\nISTART=1\nICHARG=1",
                                   extra="NUPDOWN = 0\nSYSTEM = BEXT=2\n# NUPDOWN=2\n"))
    assert a["pattern"] != "global_reversed" and any("冲突" in n for n in a["notes"])
    assert len(a["reference"]["restart_notes"]) == 2
    b = build_magnetic_analysis(run(output=(-1.8, 1.8, 0), extra="SYSTEM = BEXT=2\n# NUPDOWN=2\n"))
    assert b["pattern"] == "global_reversed"


@pytest.mark.parametrize("raw", ["0*2 2 -2 0", "2 bad 0", "2 NaN 0", "2 inf 0", "2 T 0", "10001*2", "2 -2", ""])
def test_invalid_raw_magmom_never_uses_surviving_parser_values(raw):
    parsed = run()
    parsed.incar = parse_incar("MAGMOM=" + raw)
    a = build_magnetic_analysis(parsed)
    assert not a["reference"]["valid"] and not any(r["comparison_available"] for r in a["atoms"])
    assert all(r["input_reference"] is None for r in a["atoms"])
    assert MagmomSignFlipRule().run(parsed) == [] and LocalMomentCollapseRule().run(parsed) == []
    json.dumps(_plots_compat(_build_plots(parsed)), allow_nan=False)


def test_repetition_f_column_and_api_preserve_raw_totals():
    parsed = run(reference=(2, 2, 0), output=(1, -1, 0), tables=table([1, -1, 0], f=True), extra=" number of electron 24 magnetization 0.125\n")
    parsed.incar = parse_incar("MAGMOM=2*2 0")
    parsed.oszicar.magnetic_evidence = parse_oszicar_magnetic(" 7 F=-20 E0=-20 d E=0 mag=0.25\n")
    plot = _plots_compat(_build_plots(parsed))["magnetization"]
    assert [r["tot"] for r in parsed.outcar.final_magnetization] == [1, -1, 0]
    assert plot["series"][1]["f"] == 0 and plot["series"][1]["final_moment"] == -1
    a = plot["analysis"]
    assert a["reference"]["values"] == [2, 2, 0]
    assert a["raw_rows"][1]["raw"]["tot"] == "-1" and a["raw_rows"][1]["source_line"]
    assert [t["values"] for t in a["totals"]] == [[.125], [.25], [9.999], [0]]
    assert [t["kind"] for t in a["totals"]] == ["cell_direct", "cell_direct", "projection_reported", "projection_sum"]


def test_latest_incomplete_table_does_not_fall_back_to_complete_history():
    parsed = run(tables=table([2, -2, 0]) + table([-1], closed=False), normal=False)
    a = build_magnetic_analysis(parsed)
    assert a["status"] == "partial" and a["pattern"] == "insufficient"
    assert [r["output_moment"] for r in a["atoms"]] == [-1, None, None]
    assert not a["output"]["table_complete"] and a["output"]["provisional"]
    assert len(parsed.outcar.final_magnetization) == 1
    assert a["totals"][-1]["values"] == [-1] and not a["totals"][-1]["complete"]


@pytest.mark.parametrize("ids", [[1, 1, 3], [2, 1, 3], [1, 2, 4], [0, 2, 3]])
def test_invalid_ordered_identity_blocks_pairing_but_preserves_raw(ids):
    a = build_magnetic_analysis(run(tables=table([1, 2, 3], ids=ids)))
    assert a["structure"]["mapping"] == "mismatch"
    assert [r["atom_index"] for r in a["raw_rows"]] == ids
    assert not any(r["comparison_available"] for r in a["atoms"])


def test_missing_middle_ion_uses_unique_index_and_no_global_pattern():
    a = build_magnetic_analysis(run(tables=table([1.8, 0], ids=[1, 3])))
    assert [r["output_moment"] for r in a["atoms"]] == [1.8, None, 0]
    assert a["status"] == "partial" and a["pattern"] == "insufficient"


@pytest.mark.parametrize("extra", ["NIONS = 4\n", "ions per type = 1 2\n", "VRHFIN = O:\n"])
def test_output_structure_echo_mismatch_blocks_comparison(extra):
    a = build_magnetic_analysis(run(extra=extra))
    assert a["structure"]["mapping"] == "mismatch" and not any(r["comparison_available"] for r in a["atoms"])


def test_v4_element_unknown_and_structure_truncation_remain_explicit():
    a = build_magnetic_analysis(run(structure=poscar(3, elements="")))
    assert [r["element"] for r in a["atoms"]] == ["", "", ""]
    assert a["structure"]["mapping"] == "index_aligned"
    b = build_magnetic_analysis(run(structure=poscar(3, positions=["0.00000000 0.00000000 0.00000000"])))
    assert b["status"] == "unavailable" and b["structure"]["mapping"] == "unavailable"


def test_same_element_site_order_geometry_periodic_and_precision_checks():
    positions = ["-0.50000000 0.00000000 0.00000000", "0.25000000 0.00000000 0.00000000", "0.00000000 0.00000000 0.00000000"]
    header = "position of ions in fractional coordinates (direct lattice)\n"
    valid = "0.500000 0.000000 0.000000\n0.250000 0.000000 0.000000\n0.000000 0.000000 0.000000\n\n"
    a = build_magnetic_analysis(run(structure=poscar(3, positions=positions), extra=header + valid))
    assert a["structure"]["mapping"] == "geometry_checked"
    mismatch = valid.splitlines(); mismatch[0], mismatch[1] = mismatch[1], mismatch[0]
    b = build_magnetic_analysis(run(structure=poscar(3, positions=positions), extra=header + "\n".join(mismatch) + "\n"))
    assert b["structure"]["mapping"] == "mismatch" and not any(r["comparison_available"] for r in b["atoms"])
    coarse = "0 0 0\n0 0 0\n0 0 0\n\n"
    c = build_magnetic_analysis(run(structure=poscar(3, positions=positions), extra=header + coarse))
    assert c["structure"]["mapping"] == "index_aligned"


def test_soc_vector_output_retains_components_and_scalar_mag_as_unparsed():
    parsed = run(reference=(0, 0, 2) * 3, structure=poscar(3), extra="LSORBIT = T\nLNONCOLLINEAR = T\n", tables=table([1, 2, 3]) + table([.1, .2, .3], axis="y") + table([.4, .5, .6], axis="z"))
    parsed.oszicar.magnetic_evidence = parse_oszicar_magnetic(" 1 F=-20 mag=0.1 0.2 0.3\n")
    a = build_magnetic_analysis(parsed)
    assert a["status"] == "unsupported" and len(a["raw_rows"]) == 9
    assert a["totals"][0]["values"] == [.1, .2, .3]
    assert not any(r["comparison_available"] for r in a["atoms"])
    assert a["reference"]["raw"] == "0 0 2 0 0 2 0 0 2" and len(a["reference"]["values"]) == 9
    assert all(r["input_reference"] is None and r["output_moment"] is None and r["output_raw"] is None
               and r["delta_moment"] is None and r["reference_group"] == "unknown" and r["output_group"] == "unknown" for r in a["atoms"])
    assert all(r["initial_moment"] is None and r["final_moment"] is None and r["tot"] is None and "d" not in r
               for r in _build_plots(parsed)["magnetization"]["series"])
    parsed.oszicar.magnetic_evidence = parse_oszicar_magnetic(" 1 F=-20 mag=0.1\n")
    b = build_magnetic_analysis(parsed)
    assert b["totals"][0]["values"] is None and b["totals"][0]["raw"] == ["0.1"]
    assert parsed.outcar.final_magnetization is None


def test_zero_single_atom_and_absent_sources_do_not_invent_arrangements():
    zero = build_magnetic_analysis(run(reference=(0, 0), output=(0, 0)))
    assert zero["pattern"] == "near_zero_reference" and all(r["output_moment"] == 0 for r in zero["atoms"])
    single = build_magnetic_analysis(run(reference=(2,), output=(-1.8,)))
    assert single["pattern"] == "local_changes"
    old = ParsedRunData(incar=IncarData(effective={"MAGMOM": [True, "bad"]}), outcar=OutcarData(final_magnetization=[{"tot": 1}, {"tot": None}]))
    plot = _plots_compat(_build_plots(old))["magnetization"]
    assert plot["series"][0]["atom_index"] is None and plot["series"][1]["final_moment"] is None
    assert plot["analysis"]["status"] == "unavailable"
    assert MagmomSignFlipRule().run(old) == [] and LocalMomentCollapseRule().run(old) == []


def test_real_file_load_api_mapping_retains_null_and_provenance(tmp_path):
    files = {"INCAR": "MAGMOM=2 -2 0\n", "POSCAR": poscar(3), "OUTCAR": "ISPIN = 2\n" + table([1.8, "NaN", 0]),
             "OSZICAR": " 1 F=-20 E0=-20 d E=0 mag=0.123\n"}
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    plot = _plots_compat(_build_plots(_load_parsed(tmp_path, None)))["magnetization"]
    assert plot["series"][1]["final_moment"] is None and plot["series"][2]["final_moment"] == 0
    assert plot["analysis"]["atoms"][1]["output_raw"] == "NaN"
    assert plot["analysis"]["totals"][0]["source_file"] == "OSZICAR"
    assert plot["analysis"]["reference"]["source_line"] == 1
    json.dumps(plot, allow_nan=False)


def test_standard_inline_nions_is_evidence_but_system_example_is_not():
    a = build_magnetic_analysis(run(extra="number of dos NEDOS = 301 number of ions NIONS = 4\n"))
    assert a["structure"]["mapping"] == "mismatch"
    b = build_magnetic_analysis(run(extra="SYSTEM = number of dos NEDOS = 301 number of ions NIONS = 4\n"))
    assert b["structure"]["mapping"] == "index_aligned"


def test_nonchemical_structure_labels_are_preserved_not_rejected():
    a = build_magnetic_analysis(run(structure=poscar(3, elements="Fe1"), extra="VRHFIN = Fe:\n"))
    assert a["structure"]["mapping"] == "index_aligned"
    assert all(r["element"] == "Fe1" for r in a["atoms"])
    assert any("未核验" in n for n in a["structure"]["notes"])


@pytest.mark.parametrize("raw", ["NUPDOWN=-1 bad", "I_CONSTRAINED_M=0 bad"])
def test_constraint_input_requires_complete_scalar_token(raw):
    a = build_magnetic_analysis(run(output=(-1.8, 1.8, 0), incar=raw))
    assert a["pattern"] != "global_reversed" and any("无效设置" in n for n in a["notes"])


@pytest.mark.parametrize("raw", ["LSORBIT=F T", "LNONCOLLINEAR=F bad"])
def test_mode_boolean_input_requires_complete_token(raw):
    a = build_magnetic_analysis(run(output=(-1.8, 1.8, 0), incar=raw))
    assert a["status"] == "unsupported" and a["pattern"] == "insufficient"


def test_exact_display_magnitude_threshold_has_no_binary_roundoff_exclusion():
    a = build_magnetic_analysis(run(reference=(2, -.3, .2), output=(1.6, -.2, .1)))
    assert [r["magnitude"] for r in a["atoms"]] == ["decreased", "decreased", "decreased"]
    assert a["atoms"][2]["attenuated"]
