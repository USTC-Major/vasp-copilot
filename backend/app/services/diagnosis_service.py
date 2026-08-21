from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Optional

from ..diagnostics.engine import DiagnosisEngine
from ..diagnostics.fixes import FixGenerator
from ..diagnostics.rules import all_rules
from ..parsers.incar import parse_incar
from ..parsers.job_log import parse_job_log
from ..parsers.kpoints import parse_kpoints
from ..parsers.cif import parse_cif
from ..parsers.oszicar import parse_oszicar
from ..parsers.outcar import parse_outcar
from ..parsers.poscar import parse_poscar
from ..parsers.vasprun import VASPRUN_MAX_PARSE_BYTES, parse_vasprun
from ..report.generator import ReportGenerator
from ..report.next_step import compute_next_step
from ..schemas.detected import DetectedFile, DetectedRun
from ..schemas.fix import RecommendedFix
from ..schemas.issue import Issue
from ..schemas.mode import CalculationMode
from ..schemas.parsed import ParsedRunData
from ..schemas.vasprun import VasprunInfo
from ..schemas.result import DiagnosisResult, Provenance
from ..schemas.status import DiagnosisStatus, ModeKind, Severity
from ..llm import get_explainer

_JOB_LOG_KEYWORDS = (".out", ".log", "log", "slurm", "job")
_RECOMMENDED = ("INCAR", "POSCAR", "KPOINTS", "OSZICAR", "OUTCAR")
# POTCAR 明确不在 doctor 解析/回收范围（设计 3.3/4.2/8.1），不作为缺失推荐项。

_SHA256_MAX_BYTES = 256 * 1024 * 1024


def _sha256_of(path: Path) -> Optional[str]:
    """流式 sha256 指纹；超大（>256MiB）或不可读时返回 None。"""
    try:
        if path.stat().st_size > _SHA256_MAX_BYTES:
            return None
        digest = sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2,
                   Severity.LOW: 3, Severity.INFO: 4}


def _kind_for(name: str) -> str:
    upper = name.upper()
    if upper == "INCAR":
        return "incar"
    if upper == "POSCAR":
        return "poscar"
    if upper == "KPOINTS":
        return "kpoints"
    if upper == "OSZICAR":
        return "oszicar"
    if upper == "OUTCAR":
        return "outcar"
    if upper in ("CONTCAR", "CONTCAR-"):
        return "concar"
    if upper == "POTCAR":
        return "potcar"
    if upper.endswith(".CIF"):
        return "cif"
    if upper == "VASPRUN.XML":
        return "vasprun"
    if upper.startswith("WAVECAR") or upper.startswith("CHGCAR"):
        return "other_big"
    return "other"


def detect_files(base_dir: Path) -> DetectedRun:
    files: list[DetectedFile] = []
    present: set[str] = set()
    candidate_logs: list[str] = []
    root_name = base_dir.name
    for child in sorted(base_dir.rglob("*")):
        if not child.is_file():
            continue
        rel = str(child.relative_to(base_dir)).replace("\\", "/")
        name = child.name
        kind = _kind_for(name)
        size = child.stat().st_size
        files.append(DetectedFile(name=name, kind=kind, size=size, path=rel,
                                  relative_path=rel, size_bytes=size,
                                  sha256=_sha256_of(child)))
        present.add(name.upper())
        if any(k in name for k in _JOB_LOG_KEYWORDS):
            candidate_logs.append(rel)
    missing = [r for r in _RECOMMENDED if r not in present]
    # POSCAR/CONTCAR 是结构文件对（设计 4.2/8.1），任一存在即结构就绪。
    if "CONTCAR" in present and "POSCAR" in missing:
        missing.remove("POSCAR")
    return DetectedRun(root=root_name, files=files,
                       missing_recommended=missing,
                       candidate_job_logs=candidate_logs)


def _read(base_dir: Path, names: tuple) -> Optional[str]:
    for n in names:
        p = base_dir / n
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
    return None


def _load_parsed(base_dir: Path, job_log: Optional[str]) -> ParsedRunData:
    parsed = ParsedRunData(source_files=[])

    incar_text = _read(base_dir, ("INCAR", "incar"))
    if incar_text is not None:
        parsed.incar = parse_incar(incar_text)
        parsed.source_files.append("INCAR")
    outcar_text = _read(base_dir, ("OUTCAR", "outcar"))
    if outcar_text is not None:
        parsed.outcar = parse_outcar(outcar_text)
        # MVP 7.21: OUTCAR parser is the source of the unified calc-mode summary.
        parsed.calculation_mode = parsed.outcar.calculation_mode
        parsed.source_files.append("OUTCAR")
    oszicar_text = _read(base_dir, ("OSZICAR", "oszicar"))
    if oszicar_text is not None:
        parsed.oszicar = parse_oszicar(oszicar_text)
        parsed.source_files.append("OSZICAR")
    poscar_text = _read(base_dir, ("POSCAR", "poscar"))
    if poscar_text is not None:
        parsed.poscar = parse_poscar(poscar_text)
        parsed.poscar.source_file = "POSCAR"
        parsed.source_files.append("POSCAR")
    else:
        # 结构文件对：无 POSCAR 时回退 CONTCAR（设计 4.2/PoscarParser 处理 POSCAR/CONTCAR）。
        concar_text = _read(base_dir, ("CONTCAR", "CONTCAR-", "contcar"))
        if concar_text is not None:
            parsed.poscar = parse_poscar(concar_text)
            parsed.poscar.source_file = "CONTCAR"
            parsed.source_files.append("CONTCAR")
    kpoints_text = _read(base_dir, ("KPOINTS", "kpoints"))
    if kpoints_text is not None:
        parsed.kpoints = parse_kpoints(kpoints_text)
        parsed.source_files.append("KPOINTS")
    # CIF is an accepted structure format (design 13.4: POSCAR, CIF).
    cif_path = None
    for _cand in sorted(base_dir.iterdir()):
        if _cand.is_file() and _cand.name.upper().endswith(".CIF"):
            cif_path = _cand
            break
    if cif_path is not None:
        try:
            parsed.cif = parse_cif(cif_path.read_text(encoding="utf-8", errors="replace"),
                                   source_file=cif_path.name)
            parsed.source_files.append(cif_path.name)
        except OSError:
            parsed.cif = None

    # vasprun.xml 可选（设计 3.3）：仅做存在性/简表；过大只标记不解析。
    vasprun_path = None
    for _cand in sorted(base_dir.iterdir()):
        if _cand.is_file() and _cand.name.upper() == "VASPRUN.XML":
            vasprun_path = _cand
            break
    if vasprun_path is not None:
        size = vasprun_path.stat().st_size
        if size > VASPRUN_MAX_PARSE_BYTES:
            parsed.vasprun = VasprunInfo(source_file=vasprun_path.name,
                                         present=True, size_bytes=size,
                                         truncated=True)
        else:
            try:
                parsed.vasprun = parse_vasprun(
                    vasprun_path.read_text(encoding="utf-8", errors="replace"),
                    source_file=vasprun_path.name)
            except OSError:
                parsed.vasprun = None
        parsed.source_files.append(vasprun_path.name)

    candidates = []
    if job_log:
        candidates = [job_log]
    else:
        for child in sorted(base_dir.iterdir()):
            if child.is_file() and any(k in child.name for k in _JOB_LOG_KEYWORDS):
                candidates.append(child.name)
    for c in candidates:
        p = base_dir / c
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            parsed.job_logs.append(parse_job_log(text, path=c))
            parsed.source_files.append(c)
    return parsed


def _highest_severity(issues: list[Issue]) -> str:
    if not issues:
        return "none"
    return min(issues, key=lambda i: _SEVERITY_ORDER.get(i.severity, 5)).severity.value


def _build_plots(parsed: ParsedRunData) -> dict:
    """按 MVP 7.5 构建结构化数值序列（scf + 磁化）。"""
    scf_series: list[dict] = []
    if parsed.oszicar is not None:
        # SCF 曲线只来自真实电子迭代（DAV/RMM/CG 等）；
        # 无电子行时 series 为空，不伪造电子步。
        for es in parsed.oszicar.electronic_steps:
            if es.energy is None:
                continue
            scf_series.append({
                "ionic_step": es.ionic_step,
                "electronic_step": es.electronic_step,
                "energy_ev": es.energy,
                "algorithm": es.algorithm,
            })
    mag_series: list[dict] = []
    if parsed.outcar is not None and parsed.outcar.final_magnetization:
        for atom in parsed.outcar.final_magnetization:
            mag_series.append({
                "atom_index": atom.get("ion"),
                "s": atom.get("s"), "p": atom.get("p"),
                "d": atom.get("d"), "tot": atom.get("tot"),
            })
    return {
        "scf": {"x_label": "电子步", "y_label": "能量 (eV)", "series": scf_series},
        "magnetization": {"x_label": "原子索引", "y_label": "磁矩 (μB)", "series": mag_series},
    }
def _make_summary(issues: list[Issue]) -> str:
    high = [i for i in issues
            if i.severity in (Severity.CRITICAL, Severity.HIGH)]
    med = [i for i in issues if i.severity == Severity.MEDIUM]
    if high:
        return (f"发现 {len(high)} 个高严重度问题（{', '.join(i.rule_id for i in high[:3])}），"
                f"另有 {len(med)} 个中等问题，请优先处理后再提交计算。")
    if issues:
        return f"发现 {len(issues)} 个问题（均为中/低严重度），运行基本正常。"
    return "未发现明显问题，运行健康。"


class DiagnosisService:
    """编排 解析 → 规则 → 修复 → next_step → 报告。"""

    def __init__(self) -> None:
        self._engine = DiagnosisEngine()
        self._engine.register_all(all_rules())
        self._fixer = FixGenerator()
        self._reporter = ReportGenerator()

    def run_diagnosis(self, parsed: ParsedRunData,
                      base_dir: Path, llm_explanation: bool = False,
                      settings=None):
        issues = self._engine.run(parsed)

        fix: Optional[RecommendedFix] = None
        fix_files: dict[str, str] = {}
        if parsed.incar.raw_lines:
            fix, fix_files = self._fixer.generate(
                parsed=parsed, issues=issues,
                incar_text="\n".join(parsed.incar.raw_lines))
        fixes = [fix] if fix is not None else []

        nxt = compute_next_step(issues=issues, fixes=fixes)
        result = DiagnosisResult(
            diagnosis_id="",
            diagnosis_status=DiagnosisStatus.SUCCEEDED,
            summary=_make_summary(issues),
            detected_run=detect_files(base_dir),
            issues=issues,
            recommended_fixes=fixes,
            missing_evidence=list(parsed.incar.unknown),
            next_step=nxt,
            plots=_build_plots(parsed),
            provenance=Provenance(
                parser_version="0.1.1", rule_set_version="0.1.1",
                vasp_version=parsed.outcar.vasp_version,
                vasp_binary_hint=parsed.outcar.vasp_binary_hint,
                calculation_mode=parsed.outcar.calculation_mode or CalculationMode(),
                llm_used=False, mode=ModeKind.RULE_BASED,
            ),
        )
        if llm_explanation:
            explainer = get_explainer(settings)
            if explainer is not None:
                try:
                    result.llm_explanation = explainer.explain(result)
                    result.provenance.llm_used = True
                    result.provenance.mode = ModeKind.RULE_PLUS_LLM
                except Exception:
                    result.llm_explanation = None

        body, meta = self._reporter.generate(result)
        result.report = meta
        return result, body, fix_files
