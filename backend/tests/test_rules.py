from __future__ import annotations

from app.schemas.parsed import (
    ParsedRunData, IncarData, PoscarData, OszicarData,
    OutcarData, KpointsData, JobLogData,
)
from app.schemas.mode import CalculationMode, MagnetizationAnalysisMode
from app.schemas.status import Severity
from app.diagnostics.engine import DiagnosisEngine
from app.diagnostics.rules import all_rules

RULES = {r.rule_id: r for r in all_rules()}


def get(rule_id):
    return RULES[rule_id]


def collinear():
    return CalculationMode(
        is_spin_polarized=True,
        magnetization_analysis_mode=MagnetizationAnalysisMode.COLLINEAR,
    )


def run(rule_id, **kw):
    return get(rule_id).run(ParsedRunData(**kw))


# ---------- files ----------
def test_required_file_missing_trigger_and_not():
    p = ParsedRunData(source_files=["INCAR", "POSCAR", "OUTCAR"])
    assert get("REQUIRED_FILE_MISSING").run(p) == []
    p2 = ParsedRunData(source_files=["OUTCAR"])
    iss = get("REQUIRED_FILE_MISSING").run(p2)
    assert len(iss) == 1 and iss[0].rule_id == "REQUIRED_FILE_MISSING"


def test_element_order_trigger_and_not():
    p = ParsedRunData(poscar=PoscarData(elements=["Si", "O"]),
                      incar=IncarData(effective={"MAGMOM": [0.6, 3.0]}))
    assert len(get("ELEMENT_ORDER_INCONSISTENT").run(p)) == 1
    p2 = ParsedRunData(poscar=PoscarData(elements=["Si", "O"]))
    assert get("ELEMENT_ORDER_INCONSISTENT").run(p2) == []


def test_potcar_poscar_mismatch_trigger_and_not():
    p = ParsedRunData(source_files=["POTCAR"],
                      poscar=PoscarData(elements=["Si", "O"]),
                      incar=IncarData(effective={"LDAUU": [4.0, 6.0]}))
    assert len(get("POTCAR_POSCAR_MISMATCH").run(p)) == 1
    p2 = ParsedRunData(poscar=PoscarData(elements=["Si"]))
    assert get("POTCAR_POSCAR_MISMATCH").run(p2) == []


# ---------- parameters ----------
def test_ldau_array_length_trigger_and_not():
    p = ParsedRunData(poscar=PoscarData(elements=["Si", "O"]),
                      incar=IncarData(effective={"LDAUU": [4.0]}))
    assert len(get("LDAU_ARRAY_LENGTH_MISMATCH").run(p)) == 1
    p2 = ParsedRunData(poscar=PoscarData(elements=["Si", "O"]),
                       incar=IncarData(effective={"LDAUU": [4.0, 6.0]}))
    assert get("LDAU_ARRAY_LENGTH_MISMATCH").run(p2) == []


def test_magmom_count_mismatch_trigger_and_not():
    p = ParsedRunData(poscar=PoscarData(elements=["Si", "O"], counts=[2, 4]),
                      incar=IncarData(effective={"MAGMOM": [1.0, 2.0]}))
    assert len(get("MAGMOM_COUNT_MISMATCH").run(p)) == 1
    p2 = ParsedRunData(poscar=PoscarData(elements=["Si", "O"], counts=[2, 4]),
                       incar=IncarData(effective={"MAGMOM": [1.0] * 6}))
    assert get("MAGMOM_COUNT_MISMATCH").run(p2) == []


def test_ispin_magmom_conflict_trigger_and_not():
    p = ParsedRunData(incar=IncarData(effective={"ISPIN": 1, "MAGMOM": [1.0, 2.0]}))
    assert len(get("ISPIN_MAGMOM_CONFLICT").run(p)) == 1
    p2 = ParsedRunData(incar=IncarData(effective={"ISPIN": 2, "MAGMOM": [1.0]}))
    assert get("ISPIN_MAGMOM_CONFLICT").run(p2) == []


def test_ionic_control_conflict_trigger_and_not():
    p = ParsedRunData(incar=IncarData(effective={"NSW": 100, "IBRION": -1}))
    assert len(get("IONIC_CONTROL_CONFLICT").run(p)) == 1
    p2 = ParsedRunData(incar=IncarData(effective={"NSW": 100, "IBRION": 2}))
    assert get("IONIC_CONTROL_CONFLICT").run(p2) == []


def test_ediffg_sign_semantics_trigger_and_not():
    p = ParsedRunData(incar=IncarData(effective={"EDIFFG": 0.01, "NSW": 100}))
    assert len(get("EDIFFG_SIGN_SEMANTICS").run(p)) == 1
    p2 = ParsedRunData(incar=IncarData(effective={"EDIFFG": -0.02, "NSW": 100}))
    assert get("EDIFFG_SIGN_SEMANTICS").run(p2) == []


def test_lmaxmix_too_low_for_dftu_trigger_and_not():
    p = ParsedRunData(incar=IncarData(effective={"LDAU": True, "LMAXMIX": 2}))
    assert len(get("LMAXMIX_TOO_LOW_FOR_DFTU").run(p)) == 1
    p2 = ParsedRunData(incar=IncarData(effective={"LDAU": True, "LMAXMIX": 4}))
    assert get("LMAXMIX_TOO_LOW_FOR_DFTU").run(p2) == []


def test_ismear_tetra_trigger():
    p = ParsedRunData(incar=IncarData(effective={"ISMEAR": -5}))
    assert len(get("ISMEAR_TETRA_FOR_METAL_RISK").run(p)) == 1


def test_icharg11_chgcar_missing_trigger_and_not():
    p = ParsedRunData(incar=IncarData(effective={"ICHARG": 11}))
    assert len(get("ICHARG11_CHGCAR_MISSING").run(p)) == 1
    p2 = ParsedRunData(source_files=["CHGCAR"],
                       incar=IncarData(effective={"ICHARG": 11}))
    assert get("ICHARG11_CHGCAR_MISSING").run(p2) == []


# ---------- scf / ionic ----------
def test_scf_reached_nelm_trigger_and_not():
    p = ParsedRunData(incar=IncarData(effective={"NELM": 60}),
                      oszicar=OszicarData(energy_series=[0.0, 0.1, 0.2, 0.3],
                                          last_step=60, converged=False))
    assert len(get("SCF_REACHED_NELM").run(p)) == 1
    p2 = ParsedRunData(incar=IncarData(effective={"NELM": 60}),
                       oszicar=OszicarData(energy_series=[0.0, 0.1, 0.2, 0.3],
                                           last_step=10, converged=False))
    assert get("SCF_REACHED_NELM").run(p2) == []


def test_scf_energy_oscillation_trigger_and_not():
    p = ParsedRunData(oszicar=OszicarData(energy_series=[0.0, 0.1, 0.0, 0.1, 0.0, 0.1]))
    assert len(get("SCF_ENERGY_OSCILLATION").run(p)) == 1
    p2 = ParsedRunData(oszicar=OszicarData(energy_series=[0.0, -0.01, -0.02, -0.03]))
    assert get("SCF_ENERGY_OSCILLATION").run(p2) == []


def test_ionic_reached_nsw_trigger_and_not():
    p = ParsedRunData(incar=IncarData(effective={"NSW": 50}),
                      oszicar=OszicarData(last_step=50, converged=False))
    assert len(get("IONIC_REACHED_NSW").run(p)) == 1
    p2 = ParsedRunData(incar=IncarData(effective={"NSW": 50}),
                       oszicar=OszicarData(last_step=50, converged=True))
    assert get("IONIC_REACHED_NSW").run(p2) == []


# ---------- magnetic (collinear only) ----------
def test_magmom_sign_flip_trigger_and_not():
    p = ParsedRunData(calculation_mode=collinear(),
                      incar=IncarData(effective={"MAGMOM": [2.0, 1.0]}),
                      outcar=OutcarData(final_magnetization=[{"tot": -1.5}, {"tot": 0.9}]))
    assert len(get("MAGMOM_SIGN_FLIP").run(p)) == 1
    p2 = ParsedRunData(calculation_mode=collinear(),
                       incar=IncarData(effective={"MAGMOM": [2.0]}),
                       outcar=OutcarData(final_magnetization=[{"tot": 1.5}]))
    assert get("MAGMOM_SIGN_FLIP").run(p2) == []


def test_local_moment_collapse_trigger_and_not():
    p = ParsedRunData(calculation_mode=collinear(),
                      incar=IncarData(effective={"MAGMOM": [2.0, 1.0]}),
                      outcar=OutcarData(final_magnetization=[{"tot": 0.01}, {"tot": 0.9}]))
    assert len(get("LOCAL_MOMENT_COLLAPSE").run(p)) == 1
    p2 = ParsedRunData(calculation_mode=collinear(),
                       incar=IncarData(effective={"MAGMOM": [2.0]}),
                       outcar=OutcarData(final_magnetization=[{"tot": 1.8}]))
    assert get("LOCAL_MOMENT_COLLAPSE").run(p2) == []


# ---------- scheduler ----------
def test_job_oom_trigger():
    p = ParsedRunData(job_logs=[JobLogData(path="j.log",
                           keywords=[{"category": "oom", "line": 3, "text": "killed"}] )])
    assert len(get("JOB_OOM").run(p)) == 1


def test_job_time_limit_trigger():
    p = ParsedRunData(job_logs=[JobLogData(path="j.log",
                           keywords=[{"category": "time_limit", "line": 1, "text": "time limit"}] )])
    assert len(get("JOB_TIME_LIMIT").run(p)) == 1


def test_module_not_found_trigger():
    p = ParsedRunData(job_logs=[JobLogData(path="j.log",
                           keywords=[{"category": "module", "line": 2, "text": "no module named ase"}] )])
    assert len(get("MODULE_NOT_FOUND").run(p)) == 1


def test_path_or_file_not_found_trigger():
    p = ParsedRunData(job_logs=[JobLogData(path="j.log",
                           keywords=[{"category": "path", "line": 4, "text": "no such file"}] )])
    assert len(get("PATH_OR_FILE_NOT_FOUND").run(p)) == 1


def test_parallel_config_risk_trigger_and_not():
    p = ParsedRunData(incar=IncarData(effective={"NCORE": 4, "KPAR": 0}))
    assert len(get("PARALLEL_CONFIG_RISK").run(p)) == 1
    p2 = ParsedRunData(incar=IncarData(effective={"NCORE": 4, "KPAR": 2}))
    assert get("PARALLEL_CONFIG_RISK").run(p2) == []


# ---------- core errors ----------
def test_brmix_trigger():
    p = ParsedRunData(outcar=OutcarData(
        error_lines=[{"line": 1, "text": "BRMIX: very serious problem with charge mixing"}]))
    assert len(get("BRMIX_SERIOUS_PROBLEM").run(p)) == 1


def test_zhegv_trigger():
    p = ParsedRunData(outcar=OutcarData(
        error_lines=[{"line": 2, "text": "ZHEGV: LAPACK routine failed"}]))
    assert len(get("ZHEGV_LAPACK_FAILURE").run(p)) == 1


def test_too_few_bands_trigger():
    p = ParsedRunData(outcar=OutcarData(
        error_lines=[{"line": 3, "text": "TOO FEW BANDS to fix the electron number"}]))
    assert len(get("TOO_FEW_BANDS").run(p)) == 1


def test_dav_trigger():
    p = ParsedRunData(outcar=OutcarData(
        error_lines=[{"line": 4, "text": "DAV: 1  to 1.0E+03 EDDDAV"}]))
    assert len(get("DAV_OR_EDDDAV_ERROR").run(p)) == 1


# ---------- kpoints / outcar ----------
def test_kpoints_line_mode_without_static_trigger_and_not():
    p = ParsedRunData(kpoints=KpointsData(line_mode=True, mode="Line-mode"))
    assert len(get("KPOINTS_LINE_MODE_WITHOUT_STATIC").run(p)) == 1
    p2 = ParsedRunData(source_files=["CHGCAR"],
                       kpoints=KpointsData(line_mode=True),
                       incar=IncarData(effective={"ICHARG": 11}))
    assert get("KPOINTS_LINE_MODE_WITHOUT_STATIC").run(p2) == []


def test_outcar_truncated_trigger_and_not():
    p = ParsedRunData(outcar=OutcarData(normal_termination=False, truncated=True))
    assert len(get("OUTCAR_TRUNCATED").run(p)) == 1
    p2 = ParsedRunData(outcar=OutcarData(normal_termination=True, truncated=False))
    assert get("OUTCAR_TRUNCATED").run(p2) == []


# ---------- engine invariants ----------
def test_engine_multi_issue_numbering_sorting_evidence():
    p = ParsedRunData(
        incar=IncarData(effective={"NELM": 60, "NSW": 50, "ISMEAR": -5, "LDAU": True, "LMAXMIX": 2,
                                   "NCORE": 4, "KPAR": 0}),
        oszicar=OszicarData(energy_series=[0.0, 0.1, 0.0, 0.1, 0.0], last_step=60, converged=False),
        outcar=OutcarData(normal_termination=False, truncated=True),
        kpoints=KpointsData(line_mode=True),
        poscar=PoscarData(elements=["Si"]),
        source_files=["INCAR", "POSCAR", "OUTCAR"],
        job_logs=[JobLogData(path="j.log",
                             keywords=[{"category": "oom", "line": 1, "text": "killed"}] )],
    )
    eng = DiagnosisEngine()
    eng.register_all(all_rules())
    issues = eng.run(p)
    assert issues
    seen = set()
    for i in issues:
        # every issue must carry at least one evidence
        assert i.evidence, f"{i.rule_id} missing evidence"
        assert i.issue_id not in seen, f"duplicate id {i.issue_id}"
        seen.add(i.issue_id)
        assert i.issue_id.startswith(i.rule_id + "-")
    # stable severity ordering: high before low/info
    rank = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2,
            Severity.LOW: 3, Severity.INFO: 4}
    ranks = [rank[i.severity] for i in issues]
    assert ranks == sorted(ranks)


def test_all_rules_registered_unique():
    ids = [r.rule_id for r in all_rules()]
    assert len(ids) == 27
    assert len(set(ids)) == 27

# ---------- same-source dedup (MVP 11.1 rule 8) ----------
def _engine():
    eng = DiagnosisEngine()
    eng.register_all(all_rules())
    return eng


def test_engine_dedup_links_zhegv_and_dav():
    p = ParsedRunData(outcar=OutcarData(error_lines=[
        {"line": 501, "text": "ERROR  ZHEGV: STZW failed"},
        {"line": 505, "text": "Error EDDDAV: did not converge"},
    ]))
    issues = _engine().run(p)
    by_rule = {i.rule_id: i for i in issues}
    assert "ZHEGV_LAPACK_FAILURE" in by_rule
    assert "DAV_OR_EDDDAV_ERROR" in by_rule
    z = by_rule["ZHEGV_LAPACK_FAILURE"]
    d = by_rule["DAV_OR_EDDDAV_ERROR"]
    assert z.related_issue_ids == [d.issue_id]
    assert d.related_issue_ids == [z.issue_id]
    assert z.root_cause_candidate == d.root_cause_candidate
    assert z.root_cause_candidate == "OUTCAR:501"


def test_engine_dedup_no_link_when_single_rule_fires():
    p = ParsedRunData(outcar=OutcarData(error_lines=[
        {"line": 501, "text": "ERROR  ZHEGV: STZW failed"},
    ]))
    issues = _engine().run(p)
    z = [i for i in issues if i.rule_id == "ZHEGV_LAPACK_FAILURE"][0]
    assert z.related_issue_ids == []
    assert z.root_cause_candidate is None


def test_engine_dedup_no_link_without_core_errors():
    p = ParsedRunData(outcar=OutcarData(error_lines=[]))
    issues = _engine().run(p)
    assert not [i for i in issues if i.rule_id == "ZHEGV_LAPACK_FAILURE"]
    assert not [i for i in issues if i.rule_id == "DAV_OR_EDDDAV_ERROR"]