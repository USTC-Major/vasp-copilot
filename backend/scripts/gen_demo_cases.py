"""Generate demo_cases assets per MVP 13.3.

Creates the input files + input.zip for each failed_run case, runs the real
deterministic diagnosis, and writes expected_outputs/<case_id>/ (diagnosis
result, expected rules, fix diff, demo script) plus case.yaml with the 10 fixed
fields required by the design.

Usage:  python scripts/gen_demo_cases.py
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.diagnosis_service import DiagnosisService, _load_parsed

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "demo_cases"

SHARED_INCAR = """\
SYSTEM = Si static (demo)
ISTART = 0
ICHARG = 2
ENCUT  = 400
EDIFF  = 1E-5
NELM   = 60
ISMEAR = 0
SIGMA  = 0.05
ISYM   = 2
"""
POSCAR_SI = """\
Si2
1.0
0.0 2.75 2.75
2.75 0.0 2.75
2.75 2.75 0.0
Si
2
Direct
0.0 0.0 0.0
0.25 0.25 0.25
"""
KPOINTS = """\
k-points
0
Gamma
1 1 1
0 0 0
"""


def _oszicar_series(count: int, start: float, step_d: float) -> str:
    lines = ["    NION      2",
             "    NELEC     %d" % count,
             "-" * 30]
    for i in range(1, count + 1):
        e = start - step_d * i
        d_e = -step_d
        lines.append(
            "%4d F=%.8f E0=%.8f d E=%+.8e" % (i, e, e, d_e))
    return "\n".join(lines) + "\n"


def _outcar_scf(count: int, start: float, step_d: float, brmix: bool = False) -> str:
    lines = [
        " vasp.6.3.2 (build Mar 2025) (parallel)",
        " running on   1 total cores",
        " POSCAR: Si2      (demo)",
        "  "
        "   maximum precision for lattice parameters",
        "",
        "   LATTYP: Found a simple cubic cell.",
        "   ALAT       =     5.4300000",
        "",
        "   number of electron      12",
        "",
        "   LOOP+:  electronic self-consistency",
    ]
    for i in range(1, count + 1):
        e = start - step_d * i
        lines.append(
            "   %3d  DAV:  0.100E+00  0.100E+00 -0.100E+00 %18.8f" % (i, e))
        lines.append(
            "   %3d F= %18.8f E0= %18.8f d E =%+.8e" % (i, e, e, -step_d))
    if brmix:
        lines.append("  ")
        lines.append(" BRMIX: very serious problems the old and the new charge density do not agree.")
        lines.append(" BRMIX: stopping")
    return "\n".join(lines) + "\n"



def _outcar_magmom(count: int, start: float, step_d: float) -> str:
    """Collinear spin-polarized OUTCAR whose final local moments collapse to ~0.

    Echoes ISPIN=2 so parse_outcar sets calc mode to COLLINEAR, and ends with a
    normal-termination block so OUTCAR_TRUNCATED does not fire.
    """
    header = [
        " vasp.6.3.2 (build Mar 2025) (parallel)",
        " running on   1 total cores",
        " POSCAR: Fe2      (demo)",
        "     maximum precision for lattice parameters",
        "",
        "   LATTYP: Found a body centered tetragonal cell.",
        "   ALAT       =     2.8600000",
        "",
        "   ISPIN =      2",
        "",
        "   number of electron      8",
        "",
        "   LOOP+:  electronic self-consistency",
    ]
    body = []
    for i in range(1, count + 1):
        e = start - step_d * i
        body.append("   %3d F= %18.8f E0= %18.8f d E =%+.8e" % (i, e, e, -step_d))
    mag = [
        " magnetization (x)",
        " # of ion       s       p       d       tot",
        " -------------------------------------------------",
        "    1         0.000   0.000   0.040   0.040",
        "    2         0.000   0.000   0.040   0.040",
        " -------------------------------------------------",
        " tot          0.000   0.000   0.080   0.080",
        "",
        " General timing and accounting informations for this run:",
        "     Total CPU time used (sec):        1.00",
    ]
    return "\n".join(header + body + mag) + "\n"


def _outcar_normal(count: int, start: float, step_d: float) -> str:
    """A clean OUTCAR that ends with a normal-termination block (no errors)."""
    lines = _outcar_scf(count, start, step_d).splitlines()
    lines += [
        "",
        " General timing and accounting informations for this run:",
        "     Total CPU time used (sec):        1.00",
    ]
    return "\n".join(lines) + "\n"


def _outcar_zhegv() -> str:
    """An OUTCAR that ends with a ZHEGV/LAPACK diagonalization error line."""
    return "\n".join([
        " vasp.6.3.2 (build Mar 2025) (parallel)",
        " running on   1 total cores",
        " POSCAR: Si2      (demo)",
        "     maximum precision for lattice parameters",
        "",
        "   LATTYP: Found a simple cubic cell.",
        "   ALAT       =     5.4300000",
        "",
        "   number of electron      12",
        "",
        "   LOOP+:  electronic self-consistency",
        "     1 F=      -100.05000000 E0=      -100.05000000 d E =-5.00000000e-02",
        "   1 ZHEGV: INFO=7 LAPACK routine ZHEGV failed",
        "   ZHEGV: Error in diagonalization of overlap matrix",
    ]) + "\n"


POSCAR_FE2 = """\
Fe2 (demo, bcc-like)
1.0
2.86 0.0 0.0
0.0 2.86 0.0
0.0 0.0 2.86
Fe
2
Direct
0.0 0.0 0.0
0.5 0.5 0.5
"""

MAGMOM_INCAR = SHARED_INCAR + "ISPIN  = 2\nMAGMOM = 2*2.0\n"
INCAR_ION_NSW = SHARED_INCAR + "NSW   = 5\n"

FE2O3_CIF = """\
data_Fe2O3
_cell_length_a   5.0380
_cell_length_b   5.0380
_cell_length_c   13.7720
_cell_angle_alpha 90.0000
_cell_angle_beta  90.0000
_cell_angle_gamma 120.0000
_symmetry_space_group_name_H-M  'R -3 c H'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Fe1 Fe 0.0000 0.0000 0.3553
Fe2 Fe 0.0000 0.0000 0.6447
O1   O  0.3054 0.3054 0.2500
O2   O  0.6946 0.6946 0.2500
O3   O  0.0000 0.0000 0.5000
"""

JOB_LOG_OOM = """\
JOB 12345: slurmstepd: error: Detected 1 oom-kill event(s) in StepId=12345.batch
Killed process 999999 (vasp_std) total-vm: 12800000kB
Slurm job aborted: out of memory
"""

JOB_LOG_TIME_LIMIT = """\
SLURM: slurmstepd: error: *** JOB 424242 ON cluster-vasp CANCELLED AT 2026-08-09T12:00:00 DUE TO TIME LIMIT ***
srun: Job step aborted: Reached time limit 0-00:10:00
"""



def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_zip(files: dict[str, str], dest: Path) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    dest.write_bytes(buf.getvalue())
    return sha256_bytes(buf.getvalue())


def run_diagnosis(input_files: dict[str, str]):
    with tempfile.TemporaryDirectory(prefix="vd_demo_") as td:
        root = Path(td)
        for name, text in input_files.items():
            (root / name).write_text(text, encoding="utf-8")
        svc = DiagnosisService()
        parsed = _load_parsed(root, None)
        result, _body, fix_files = svc.run_diagnosis(parsed, root)
        return result, fix_files


def yaml_list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join('"%s"' % i for i in items) + "]"


def write_case_assets(case: dict, input_files: dict[str, str]) -> None:
    case_id = case["case_id"]
    case_dir = DEMO / "failed_runs" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    for name, text in input_files.items():
        (case_dir / name).write_text(text, encoding="utf-8")

    sha = make_zip(input_files, case_dir / "input.zip")

    result, fix_files = run_diagnosis(input_files)
    issues = result.issues
    rule_ids = sorted({i.rule_id for i in issues})
    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for i in issues:
        key = i.severity.value
        if key in sev_counts:
            sev_counts[key] += 1

    default_fix = next((f for f in result.recommended_fixes if f.safe_to_generate), None)
    fix_diff = default_fix.diff if (default_fix and default_fix.diff) else \
        "# (no auto-fix diff for this case; see expected_rules.json)\n"

    expected_dir = DEMO / "expected_outputs" / case_id
    expected_dir.mkdir(parents=True, exist_ok=True)
    (expected_dir / "diagnosis_result.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8")
    rules_payload = [{
        "rule_id": i.rule_id,
        "severity": i.severity.value,
        "blocking": i.blocking,
        "auto_fixable": i.auto_fixable,
        "confidence": round(i.confidence, 3),
        "evidence_files": sorted({e.file for e in i.evidence}),
    } for i in issues]
    (expected_dir / "expected_rules.json").write_text(
        json.dumps(rules_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (expected_dir / "expected_fix.diff").write_text(fix_diff, encoding="utf-8")

    script_md = (
        "# Demo script: %s\n\n"
        "- upload `%s/input.zip` to `POST /api/v1/diagnosis/upload`\n"
        "- run `POST /api/v1/diagnosis/run`\n"
        "- `GET /api/v1/diagnosis/{id}` then `GET .../report`\n"
        "- expected rule_ids: %s\n"
        "- expected issue_count_by_severity: %s\n"
        "- fix availability: %s\n" % (
            case_id, case_id, ", ".join(rule_ids) or "(none)",
            json.dumps(sev_counts, ensure_ascii=False),
            "safe fix available" if default_fix else "no safe auto-fix"))
    (expected_dir / "demo_script.md").write_text(script_md, encoding="utf-8")

    missing = list(result.missing_evidence)
    case_yaml = (
        "case_id: %s\n"
        "description: %s\n"
        "input_sha256: %s\n"
        "required_feature_flags: %s\n"
        "expected_rule_ids: %s\n"
        "expected_issue_count_by_severity: %s\n"
        "recommended_fix_expected: %s\n"
        "unsupported_or_missing_evidence: %s\n"
        "demo_phrase: %s\n"
        "max_runtime_seconds: %d\n" % (
            case_id,
            json.dumps(case["description"], ensure_ascii=False),
            sha,
            yaml_list([]),
            yaml_list(rule_ids),
            json.dumps(sev_counts, ensure_ascii=False),
            "true" if default_fix else "false",
            yaml_list(missing),
            json.dumps(case["demo_phrase"], ensure_ascii=False),
            case["max_runtime_seconds"]))
    (case_dir / "case.yaml").write_text(case_yaml, encoding="utf-8")

    print("[%s] rules=%s counts=%s fix=%s sha=%s" % (
        case_id, rule_ids, sev_counts, "Y" if default_fix else "N", sha[:12]))


def write_structures() -> None:
    struct = DEMO / "structures" / "fe2o3_poscar"
    struct.mkdir(parents=True, exist_ok=True)
    poscar = """\
Fe2O3 (demo, 5 atoms)
1.0
5.0380 0.0000 0.0000
0.0000 5.0380 0.0000
0.0000 0.0000 13.7720
Fe O
2 3
Direct
0.0000 0.0000 0.3553
0.0000 0.0000 0.6447
0.3054 0.3054 0.2500
0.6946 0.6946 0.2500
0.0000 0.0000 0.5000
"""
    (struct / "POSCAR").write_text(poscar, encoding="utf-8")
    sha = sha256_bytes(poscar.encode("utf-8"))
    case_yaml = (
        "case_id: fe2o3_poscar\n"
        "description: \"Structure sample (Fe2O3, 5 atoms POSCAR) used by Doctor for structure-side analysis; not a failed run.\"\n"
        "input_sha256: %s\n"
        "required_feature_flags: []\n"
        "expected_rule_ids: []\n"
        "expected_issue_count_by_severity: {}\n"
        "recommended_fix_expected: false\n"
        "unsupported_or_missing_evidence: []\n"
        "demo_phrase: \"Structure input sample; pair with a failed run to show structure-side context.\"\n"
        "max_runtime_seconds: 0\n" % sha)
    (struct / "case.yaml").write_text(case_yaml, encoding="utf-8")
    print("[structure] fe2o3_poscar sha=%s" % sha[:12])

    cif_dir = DEMO / "structures" / "sample_cif"
    cif_dir.mkdir(parents=True, exist_ok=True)
    (cif_dir / "Fe2O3.cif").write_text(FE2O3_CIF, encoding="utf-8")
    cif_sha = sha256_bytes(FE2O3_CIF.encode("utf-8"))
    cif_yaml = (
        "case_id: sample_cif\n"
        "description: \"Structure sample (Fe2O3 CIF) used by Doctor for structure-side analysis; not a failed run.\"\n"
        "input_sha256: %s\n"
        "required_feature_flags: []\n"
        "expected_rule_ids: []\n"
        "expected_issue_count_by_severity: {}\n"
        "recommended_fix_expected: false\n"
        "unsupported_or_missing_evidence: []\n"
        "demo_phrase: \"Structure input sample (CIF); pair with a failed run to show structure-side context.\"\n"
        "max_runtime_seconds: 0\n" % cif_sha)
    (cif_dir / "case.yaml").write_text(cif_yaml, encoding="utf-8")
    print("[structure] sample_cif sha=%s" % cif_sha[:12])


CASES = [
    {
        "case_id": "scf_reached_nelm",
        "description": "电子步达到 NELM=60 仍未收敛：展示 SCF 未收敛主路径与 ALGO/混合建议。",
        "demo_phrase": "上传后运行诊断：应命中 SCF_REACHED_NELM，报告给出收敛建议。",
        "max_runtime_seconds": 30,
        "files": {
            "INCAR": SHARED_INCAR,
            "POSCAR": POSCAR_SI,
            "KPOINTS": KPOINTS,
            "OSZICAR": _oszicar_series(60, -100.0, 0.05),
            "OUTCAR": _outcar_scf(60, -100.0, 0.05),
        },
    },
    {
        "case_id": "brmix_problem",
        "description": "OUTCAR 报 BRMIX 严重问题：展示电荷混合失败的核心错误判定。",
        "demo_phrase": "上传后运行诊断：应命中 BRMIX_SERIOUS_PROBLEM，报告给出人工排查提示。",
        "max_runtime_seconds": 30,
        "files": {
            "INCAR": SHARED_INCAR,
            "POSCAR": POSCAR_SI,
            "KPOINTS": KPOINTS,
            "OSZICAR": _oszicar_series(12, -100.0, 0.05),
            "OUTCAR": _outcar_scf(12, -100.0, 0.05, brmix=True),
        },
    },
    {
        "case_id": "outcar_truncated",
        "description": "OUTCAR 被截断（无正常结束标志）：展示输出完整性检查。",
        "demo_phrase": "上传后运行诊断：应命中 OUTCAR_TRUNCATED，提示补齐完整输出。",
        "max_runtime_seconds": 30,
        "files": {
            "INCAR": "SYSTEM = Si static (demo)\nISTART = 0\nICHARG = 2\nENCUT  = 400\nEDIFF  = 1E-5\nNELM   = 60\nISMEAR = 0\nSIGMA  = 0.05\n",
            "POSCAR": POSCAR_SI,
            "KPOINTS": KPOINTS,
            "OSZICAR": _oszicar_series(8, -100.0, 0.05),
            "OUTCAR": _outcar_scf(8, -100.0, 0.05),
        },
    },
    {
        "case_id": "magmom_collapse_collinear",
        "description": "Collinear spin collapse: ISPIN=2 + MAGMOM=2*2.0 but final local moments ~0.04 (LOCAL_MOMENT_COLLAPSE). Demonstrate magnetic-moment collapse notice (design 13.3).",
        "demo_phrase": "Upload then run diagnosis: expect LOCAL_MOMENT_COLLAPSE; report asks to verify magnetic state.",
        "max_runtime_seconds": 30,
        "files": {
            "INCAR": MAGMOM_INCAR,
            "POSCAR": POSCAR_FE2,
            "KPOINTS": KPOINTS,
            "OSZICAR": _oszicar_series(6, -50.0, 0.05),
            "OUTCAR": _outcar_magmom(6, -50.0, 0.05),
        },
    },
    {
        "case_id": "job_oom",
        "description": "Job-level OOM: run.log shows out-of-memory / killed process (JOB_OOM). Demonstrate scheduler-level failure detection.",
        "demo_phrase": "Upload then run diagnosis: expect JOB_OOM; report gives memory / NCORE-KPAR tuning hints.",
        "max_runtime_seconds": 30,
        "files": {
            "INCAR": SHARED_INCAR,
            "POSCAR": POSCAR_SI,
            "KPOINTS": KPOINTS,
            "OSZICAR": _oszicar_series(8, -100.0, 0.05),
            "OUTCAR": _outcar_normal(8, -100.0, 0.05),
            "run.log": JOB_LOG_OOM,
        },
    },
    {
        "case_id": "zhegv_lapack_failure",
        "description": "OUTCAR ends with ZHEGV/LAPACK diagonalization error (ZHEGV_LAPACK_FAILURE). Demonstrate core-error rule (blocking, review-only).",
        "demo_phrase": "Upload then run diagnosis: expect ZHEGV_LAPACK_FAILURE (plus OUTCAR_TRUNCATED); report asks to review structure/precision.",
        "max_runtime_seconds": 30,
        "files": {
            "INCAR": SHARED_INCAR,
            "POSCAR": POSCAR_SI,
            "KPOINTS": KPOINTS,
            "OSZICAR": _oszicar_series(4, -100.0, 0.05),
            "OUTCAR": _outcar_zhegv(),
        },
    },
    {
        "case_id": "job_time_limit",
        "description": "Job-level walltime limit: run.log shows DUE TO TIME LIMIT (JOB_TIME_LIMIT). Demonstrate scheduler time-limit failure detection.",
        "demo_phrase": "Upload then run diagnosis: expect JOB_TIME_LIMIT; report asks to increase walltime or split/continue the calculation.",
        "max_runtime_seconds": 30,
        "files": {
            "INCAR": SHARED_INCAR,
            "POSCAR": POSCAR_SI,
            "KPOINTS": KPOINTS,
            "OSZICAR": _oszicar_series(8, -100.0, 0.05),
            "OUTCAR": _outcar_normal(8, -100.0, 0.05),
            "run.log": JOB_LOG_TIME_LIMIT,
        },
    },
    {
        "case_id": "ion_reached_nsw",
        "description": "Ionic steps reached NSW=5 without convergence (IONIC_REACHED_NSW). Demonstrate incomplete geometry-optimization detection.",
        "demo_phrase": "Upload then run diagnosis: expect IONIC_REACHED_NSW; report suggests reviewing force/SCF trend and extending NSW only after checks.",
        "max_runtime_seconds": 30,
        "files": {
            "INCAR": INCAR_ION_NSW,
            "POSCAR": POSCAR_SI,
            "KPOINTS": KPOINTS,
            "OSZICAR": _oszicar_series(5, -100.0, 0.05),
            "OUTCAR": _outcar_normal(5, -100.0, 0.05),
        },
    },
]



def main() -> None:
    (DEMO / "failed_runs").mkdir(parents=True, exist_ok=True)
    for case in CASES:
        write_case_assets(case, case["files"])
    write_structures()
    print("done ->", DEMO)


if __name__ == "__main__":
    main()