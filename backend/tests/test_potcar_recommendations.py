"""PC-R reviewed-rule scenarios. Synthetic metadata only, no licensed datasets."""
import json

import pytest

from backend.toolbox.potcar import recommendations as rules
from backend.tests.test_potcar_assembly import setup, preview, confirmation, poscar
from backend.tests.test_potcar_library import assert_error, put, scan, synthetic


def candidates(*variants):
    return [{'dataset_id': str(i).zfill(32), 'variant': v, 'status': 'ready'} for i, v in enumerate(variants, 1)]


def choose(element, *variants, ctx=None, selected=None):
    return rules.choose(element, candidates(*variants), selected,
                        rules.context(ctx or {'purpose': 'regular', 'functional': 'PBE'}))


@pytest.mark.parametrize('element,target', [('In', 'In_d'), ('Ga', 'Ga_d'), ('Ge', 'Ge_d'), ('Sn', 'Sn_d'),
    *[(e, e) for e in ('B', 'C', 'N', 'O', 'F')], ('Li', 'Li_sv'),
    *[(e, e + '_sv') for e in ('Rb', 'Sr', 'Cs', 'Ba')]])
def test_r01_r04_r05_r06_finite_rules(element, target):
    choices = [element + '_GW', target, element + '_h']
    selected, reason, _, advice = choose(element, *choices)
    assert selected == candidates(*choices)[1]['dataset_id']
    assert reason == 'RULE_RECOMMENDED'
    assert 'RELEASE_UNKNOWN' in {a['code'] for a in advice}
    assert not any('V64' in r for a in advice for r in a['rule_ids'])


def test_r02_explicit_does_not_hide_guidance():
    selected, reason, _, advice = choose('In', 'In', 'In_d', selected='1'.zfill(32))
    assert selected == '1'.zfill(32) and reason == 'USER_SELECTED'
    assert any(a['target_variants'] == ['In_d'] for a in advice)


def test_r03_missing_does_not_fall_back():
    selected, reason, _, advice = choose('In', 'In')
    assert selected is None and reason == 'SELECTION_REQUIRED'
    assert 'RECOMMENDED_MISSING' in {a['code'] for a in advice}


@pytest.mark.parametrize('element', ['Fe', 'Sc', 'Ti', 'V', 'Cr', 'Mn'])
def test_r07_r08_r09_version_table_intentionally_disabled(element):
    assert choose(element, element, element + '_pv', element + '_sv')[0] is None


@pytest.mark.parametrize('element', ['Be', 'Mg', 'K', 'Ca', 'At'])
def test_r10_conflicts_never_unique_fallback(element):
    result = choose(element, element)
    assert result[0] is None
    assert 'RULE_CONFLICT' in {a['code'] for a in result[3]}


@pytest.mark.parametrize('status', ['ready', 'ambiguous'])
def test_r11_r12_multiple_sources_remain_manual(status):
    rows = candidates('In_d', 'In_d')
    for row in rows:
        row['status'] = status
    out = rules.choose('In', rows, None, rules.context({'purpose': 'regular', 'functional': 'PBE'}))
    assert out[0] is None
    assert 'RECOMMENDED_AMBIGUOUS' in {a['code'] for a in out[3]}


def test_r13_dftu_not_d_suffix():
    out = choose('Ni', 'Ni', 'Ni_pv', ctx={'purpose': 'regular', 'functional': 'PBE+U'})
    assert out[0] is None
    assert 'DFTU_CHANNEL_REVIEW' in {a['code'] for a in out[3]}


def test_r15_hybrid_soft_never_auto_and_manual_warns():
    ctx = {'purpose': 'regular', 'functional': 'HSE06'}
    assert choose('O', 'O', 'O_s', 'O_h', ctx=ctx)[0] == '1'.zfill(32)
    assert choose('O', 'O_s', ctx=ctx)[0] is None
    out = choose('O', 'O_s', ctx=ctx, selected='1'.zfill(32))
    assert out[0] and 'HYBRID_SOFT_AVOID' in {a['code'] for a in out[3]}
    assert choose('Si', 'Si_s', ctx=ctx)[0] is None


@pytest.mark.parametrize('key,code', [('short_bonds', 'SHORT_BONDS_REVIEW'),
    ('high_pressure', 'PRESSURE_REVIEW'), ('high_unoccupied', 'UNOCCUPIED_REVIEW')])
def test_r17_r18_r19_explicit_special_context_suppresses_regular(key, code):
    out = choose('O', 'O', 'O_h', 'O_GW', ctx={'purpose': 'regular', 'functional': 'PBE', key: True})
    assert out[0] is None and code in {a['code'] for a in out[3]}


def test_r16_no_magnetism_inferred_from_element():
    out = choose('Fe', 'Fe', 'Fe_pv')
    assert 'MAGNETIC_REVIEW' not in {a['code'] for a in out[3]}
    out = choose('Fe', 'Fe', 'Fe_pv', ctx={'purpose': 'regular', 'functional': 'PBE', 'magnetic_energy': True})
    assert out[0] is None and 'MAGNETIC_REVIEW' in {a['code'] for a in out[3]}


def test_r17_s_not_automatically_hard():
    assert choose('S', 'S', 'S_h')[0] is None
    out = choose('S', 'S', 'S_h', ctx={'purpose': 'regular', 'functional': 'PBE', 'short_bonds': True})
    assert out[0] is None and any(a['target_variants'] == ['S_h'] for a in out[3])


def test_r20_conflicting_active_rules(monkeypatch):
    monkeypatch.setattr(rules, 'RULES', rules.RULES + (('TEST-CONFLICT', {'In': 'In'}, ('S1',)),))
    out = choose('In', 'In_d')
    assert out[0] is None and 'RULE_CONFLICT' in {a['code'] for a in out[3]}


def test_r23_context_and_rule_version_pin(setup, monkeypatch):
    svc, lid, _ = setup
    unknown = preview(svc, lid)
    regular = preview(svc, lid, context={'purpose': 'regular', 'functional': 'PBE'})
    special = preview(svc, lid, context={'purpose': 'special', 'functional': 'PBE'})
    assert len({v['selection_digest'] for v in (unknown, regular, special)}) == 3
    monkeypatch.setattr(rules, 'RULE_VERSION', 'paw-pbe-selection-r2')
    assert_error('RULE_VERSION_CHANGED', lambda: svc.generate(confirmation(regular)))


def test_r24_f_and_unknown_variants():
    out = choose('Eu', 'Eu_2', 'Eu_3')
    assert out[0] is None and 'SPECIAL_VARIANT_REVIEW' in {a['code'] for a in out[3]}
    out = choose('Si', 'Si_new', 'Si')
    assert out[0] is None and 'UNKNOWN_VARIANT' in {a['code'] for a in out[3]}
    assert choose('In', 'In_AE', 'In_d')[0] == '2'.zfill(32)


def test_r25_unknown_legacy_context_not_regular(setup):
    svc, lid, root = setup
    put(root, 'In/POTCAR', synthetic('In'))
    put(root, 'In_d/POTCAR', synthetic('In_d'))
    scan(svc, lid)
    old = preview(svc, lid, poscar(('In',)))
    assert old['context']['purpose'] == 'unknown' and old['context']['short_bonds'] is None
    assert old['context_source'] == 'unknown' and old['rows'][0]['dataset_id'] is None
    assert next(s for s in old['rule_sources'] if s['source_id'] == 'S1')['url'].endswith('oldid=38073')
    regular = preview(svc, lid, poscar(('In',)), context={'purpose': 'regular', 'functional': 'PBE'})
    assert regular['rows'][0]['reason']['code'] == 'RULE_RECOMMENDED'


def test_pre_r1_snapshot_still_downloads_and_not_workflow_bound(setup):
    svc, lid, _ = setup
    artifact = svc.generate(confirmation(preview(svc, lid)))['artifact']
    path = svc.assembly.root / artifact['artifact_id'] / 'manifest.json'
    stored = json.loads(path.read_text('utf-8'))
    for key in ('rule_version', 'context', 'context_source', 'workflow_binding'):
        del stored['artifact'][key]
    path.write_text(json.dumps(stored), encoding='utf-8')
    assert svc.download(artifact['artifact_id'])
    assert svc.artifact(artifact['artifact_id'])['artifact'].get('workflow_binding') is None


def test_pressure_does_not_recommend_gw():
    out = choose('O', 'O_GW', ctx={'purpose': 'regular', 'functional': 'PBE', 'high_pressure': True})
    assert next(a for a in out[3] if a['code'] == 'PRESSURE_REVIEW')['target_variants'] == []


@pytest.mark.parametrize('key,value', [('purpose', []), ('purpose', {}), ('functional', []), ('functional', {})])
def test_context_invalid_types_are_request_errors(key, value):
    assert_error('INVALID_REQUEST', lambda: rules.context({key: value}))
