"""Minimal regressions from the historical constrained-moment case audit.

Only diagnostic evidence is reproduced here, not full research outputs.
"""
import pytest

from app.diagnostics.rules import all_rules
from app.parsers.incar import parse_incar
from app.parsers.outcar import parse_outcar
from app.schemas.parsed import ParsedRunData, PoscarData
from backend.toolbox.report.extract import verify_run

RULES = {rule.rule_id: rule for rule in all_rules()}
WARNING = 'WARNING in EDDRMM: call to ZHEGV failed, returncode =   8  4    939'
FOOTER = 'General timing and accounting informations for this job:'


@pytest.mark.parametrize('flag', ['LNONCOLLINEAR', 'LSORBIT'])
@pytest.mark.parametrize('natoms', [3, 168])
def test_vector_magmom_has_three_components_per_atom(flag, natoms):
    parsed = ParsedRunData(
        incar=parse_incar(f'{flag} = .TRUE.\nMAGMOM = {3 * natoms}*1.0\nISPIN = 1'),
        poscar=PoscarData(counts=[natoms]),
    )
    assert RULES['MAGMOM_COUNT_MISMATCH'].run(parsed) == []
    assert RULES['ISPIN_MAGMOM_CONFLICT'].run(parsed) == []


@pytest.mark.parametrize('flag', ['LNONCOLLINEAR', 'LSORBIT'])
def test_scalar_magmom_remains_wrong_in_vector_mode(flag):
    parsed = ParsedRunData(incar=parse_incar(f'{flag} = T\nMAGMOM = 3*1'),
                           poscar=PoscarData(counts=[3]))
    issues = RULES['MAGMOM_COUNT_MISMATCH'].run(parsed)
    assert len(issues) == 1
    assert '9' in issues[0].summary


@pytest.mark.parametrize('flag', ['LNONCOLLINEAR', 'LSORBIT'])
def test_mode_falls_back_to_outcar_if_incar_flag_missing(flag):
    parsed = ParsedRunData(incar=parse_incar('MAGMOM = 9*1\nISPIN = 1'),
                           poscar=PoscarData(counts=[3]),
                           outcar=parse_outcar(f'{flag} = T'))
    assert RULES['MAGMOM_COUNT_MISMATCH'].run(parsed) == []
    assert RULES['ISPIN_MAGMOM_CONFLICT'].run(parsed) == []


def test_explicit_false_is_not_truthy_and_collinear_errors_are_retained():
    parsed = ParsedRunData(incar=parse_incar('LNONCOLLINEAR = .FALSE.\nLSORBIT = F\nMAGMOM = 9*1\nISPIN = 1'),
                           poscar=PoscarData(counts=[3]))
    assert len(RULES['MAGMOM_COUNT_MISMATCH'].run(parsed)) == 1
    assert len(RULES['ISPIN_MAGMOM_CONFLICT'].run(parsed)) == 1


@pytest.mark.parametrize('has_footer', [False, True])
def test_warning_evidence_is_independent_of_output_completeness(has_footer):
    text = WARNING + ('\n' + FOOTER if has_footer else '')
    parsed = ParsedRunData(outcar=parse_outcar(text))
    assert parsed.outcar.normal_termination is has_footer
    assert parsed.outcar.truncated is (not has_footer)
    assert parsed.outcar.error_lines == [{'line': 1, 'text': WARNING}]
    assert bool(RULES['OUTCAR_TRUNCATED'].run(parsed)) is (not has_footer)
    assert len(RULES['ZHEGV_LAPACK_FAILURE'].run(parsed)) == 1


def test_complete_output_with_numerical_failure_never_becomes_success():
    outcar = f'EDIFF = 1E-5\nNELM = 120\nNSW = 0\nIBRION = -1\n{WARNING}\nfree  energy   TOTEN = -803.393729071 eV\n{FOOTER}'
    oszicar = 'RMM: 120 -0.803393729071E+03 0.28812E-02 -0.11264E-04 97392 0.998E-03\n1 F= -.803393729071E+03 E0= -.803393729071E+03 d E= 0.0'
    result = verify_run(outcar, oszicar, job_kind='static')
    assert result['normal_termination'] is True
    assert result['electronic_converged'] is False
    assert result['status'] == 'failed'
    assert any(i['rule_id'] == 'ZHEGV_LAPACK_FAILURE' for i in result['issues'])


def test_finished_smoke_test_still_fails_electronic_convergence():
    result = verify_run(
        f'EDIFF = 1E-5\nNELM = 20\nNSW = 0\nIBRION = -1\nfree  energy   TOTEN = -2.76257683622 eV\n{FOOTER}',
        'DAV: 20 -0.276257683622E+01 0.54577E+00 -0.26656E-01 672 0.367E+00\n1 F= -.276257683622E+01 E0= -.276257683622E+01 d E= 0.0',
        job_kind='static',
    )
    assert result['normal_termination'] is True
    assert result['electronic_converged'] is False
    assert result['status'] == 'not_converged'


@pytest.mark.parametrize('has_footer', [False, True])
def test_unclassified_error_evidence_survives_completeness_change(has_footer):
    error = 'Fatal error: synthetic review evidence'
    parsed = ParsedRunData(outcar=parse_outcar(error + ('\n' + FOOTER if has_footer else '')))
    issues = RULES['OUTCAR_UNCLASSIFIED_ERROR'].run(parsed)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity.value == 'medium'
    assert issue.blocking is False and issue.auto_fixable is False
    assert issue.possible_causes == []
    assert [(e.file, e.line, e.message) for e in issue.evidence] == [('OUTCAR', 1, error)]
    assert all(r.action == 'review' and r.target == 'user' for r in issue.recommendations)
    assert '截断' not in issue.title + issue.summary
    assert bool(RULES['OUTCAR_TRUNCATED'].run(parsed)) is (not has_footer)
    assert verify_run(error + '\n' + FOOTER)['status'] == 'failed'


@pytest.mark.parametrize('line,rule_id', [
    ('BRMIX: very serious problems', 'BRMIX_SERIOUS_PROBLEM'),
    (WARNING, 'ZHEGV_LAPACK_FAILURE'),
    ('TOO FEW BANDS', 'TOO_FEW_BANDS'),
    ('Error EDDDAV: synthetic evidence', 'DAV_OR_EDDDAV_ERROR'),
])
def test_specific_outcar_errors_do_not_gain_duplicate_generic_issue(line, rule_id):
    parsed = ParsedRunData(outcar=parse_outcar(line + '\n' + FOOTER))
    assert RULES[rule_id].run(parsed)
    assert RULES['OUTCAR_UNCLASSIFIED_ERROR'].run(parsed) == []


def test_mixed_errors_aggregate_only_unclassified_lines():
    lines = [WARNING, 'Fatal error: first synthetic evidence',
             'TOO FEW BANDS', 'error reading synthetic input']
    parsed = ParsedRunData(outcar=parse_outcar('\n'.join([*lines, FOOTER])))
    issues = RULES['OUTCAR_UNCLASSIFIED_ERROR'].run(parsed)
    assert len(issues) == 1
    assert [(e.line, e.message) for e in issues[0].evidence] == [(2, lines[1]), (4, lines[3])]
    assert RULES['ZHEGV_LAPACK_FAILURE'].run(parsed)
    assert RULES['TOO_FEW_BANDS'].run(parsed)


def test_clean_footer_does_not_create_unclassified_error_issue():
    parsed = ParsedRunData(outcar=parse_outcar(FOOTER))
    assert RULES['OUTCAR_UNCLASSIFIED_ERROR'].run(parsed) == []
