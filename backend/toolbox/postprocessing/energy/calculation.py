"""Energy accounting with explicit composition, references and field policy."""
from __future__ import annotations

import hashlib
import json
import math
import re

from ..store import fail

FIELDS = ('sigma_to_zero_ev', 'without_entropy_ev', 'free_energy_toten_ev')
ROLES = {'clean_slab', 'adsorbate', 'adsorbed', 'material', 'element_reference'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def composition(value):
    if (not isinstance(value, dict) or not value or len(value) > 118
            or any(not re.fullmatch(r'[A-Z][a-z]?', k) or type(v) is not int or not 0 < v <= 1_000_000
                   for k, v in value.items())):
        fail('组成必须是明确元素及其正整数原子计数', 'ENERGY_COMPOSITION_REQUIRED')
    # Validate element identity without consulting any remote source.
    from pymatgen.core import Element
    try:
        for symbol in value:
            Element(symbol)
    except (ValueError, KeyError):
        fail('组成含未知元素', 'ENERGY_COMPOSITION_REQUIRED')
    return value


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def effective(sample):
    parsed, override = sample['parsed'], sample.get('override') or {}
    fields = dict(parsed['energy_fields'])
    if override.get('energy_fields') is not None:
        # Explicit nulls in an override do not manufacture a value or fall back.
        fields.update(override['energy_fields'])
    return {'composition': override.get('composition') or parsed.get('composition'),
            'energy_fields': fields,
            'manual_energy_basis': override.get('energy_basis') or sample.get('energy_basis'),
            'composition_origin': 'manual_override' if override.get('composition') else sample['source']['kind'],
            'energy_origin': 'manual_override' if override.get('energy_fields') is not None else sample['source']['kind']}


def groups_for(sample_id, groups):
    return [g for g in groups if sample_id in ({t['sample_id'] for t in g['targets']}
            | {g.get('clean_sample_id'), g.get('adsorbate_sample_id')}
            | set(g.get('element_references', {}).values()))]


def confirmation(sample, groups):
    return fingerprint({'source': sample['source'], 'parsed': sample['parsed'], 'override': sample.get('override'),
                        'role': sample['role'], 'included': sample['included'],
                        'groups': groups_for(sample['id'], groups)})


def status_warnings(sample):
    status = sample['parsed']['status']
    warnings = list(sample['parsed'].get('warnings') or [])
    if status.get('completion') != 'completed':
        warnings.append('运行未完成或结束状态未知')
    if status.get('electronic_converged') is not True:
        warnings.append('所选步电子收敛未确认' if status.get('electronic_converged') is None else '所选步电子未收敛')
    if status.get('ionic_applicability') != 'not_applicable' and status.get('ionic_converged') is not True:
        warnings.append('离子收敛未确认' if status.get('ionic_converged') is None else '离子未收敛')
    return list(dict.fromkeys(warnings))


def validate_source(sample, basis, groups, expected_role):
    if not sample['included'] or not sample['confirmed'] or sample['role'] != expected_role:
        fail(sample['name'] + ' 尚未纳入并确认正确角色', 'ENERGY_CONFIRMATION_REQUIRED', 409)
    if sample.get('confirmation_fingerprint') != confirmation(sample, groups):
        fail(sample['name'] + ' 的来源或计算定义已改变，请重新确认', 'ENERGY_CONFIRMATION_STALE', 409)
    parsed = sample['parsed']
    eff = effective(sample)
    issues = parsed.get('issues') or []
    repairable = {i.get('code') for i in issues if i.get('recoverable_by_manual') is True
                  and (sample.get('override') or {}).get('composition')}
    errors = [i for i in issues if i.get('severity') == 'error' and i.get('code') not in repairable]
    support_reasons = ((parsed.get('metadata') or {}).get('support') or {}).get('reasons') or []
    hard_reasons = [reason for reason in support_reasons if reason not in repairable]
    if errors or hard_reasons or (parsed.get('errors') and not issues):
        detail = '；'.join(i.get('message', '') for i in errors)
        if hard_reasons:
            detail += '（' + ', '.join(hard_reasons) + '）'
        fail(sample['name'] + ' 含不可豁免的解析或方法证据缺口：' + detail +
             '。请核对来源；无完整能量块时可创建独立人工样本并保留原引用。', 'ENERGY_SOURCE_UNSUPPORTED')
    composition(eff['composition'])
    if basis not in FIELDS or not finite(eff['energy_fields'].get(basis)):
        fail(sample['name'] + ' 缺少组级选定的有限能量字段', 'ENERGY_FIELD_REQUIRED')
    if eff['manual_energy_basis'] and eff['manual_energy_basis'] != basis:
        fail(sample['name'] + ' 的人工能量口径与组级字段不同', 'ENERGY_BASIS_CONFLICT')
    warnings = status_warnings(sample)
    if warnings and not sample['accepted_warnings']:
        fail(sample['name'] + ' 的未完成或未确认状态需要明确纳入', 'ENERGY_WARNING_ACCEPTANCE_REQUIRED', 409)
    return eff, [sample['name'] + '：' + w for w in warnings]


def comparability(samples):
    warnings = []
    methods = set()
    datasets, u_values = {}, {}
    for sample in samples:
        metadata = sample['parsed'].get('metadata') or {}
        method = metadata.get('method')
        if method and method != 'unknown':
            methods.add(method.replace('+U', ''))
        else:
            warnings.append(sample['name'] + '：方法元数据未知，不能声称可比性已验证')
        parameters = metadata.get('parameters') or {}
        for key in ('LSORBIT', 'LNONCOLLINEAR'):
            if parameters.get(key) is True:
                fail('首版不支持 SOC／非共线能量自动比较', 'ENERGY_METHOD_CONFLICT')
        potcar = metadata.get('potcar_datasets') or {}
        if not potcar:
            warnings.append(sample['name'] + '：赝势数据集元数据不足')
        for element, definition in potcar.items():
            if element in datasets and datasets[element] != definition:
                fail('同元素赝势数据集定义冲突：' + element, 'ENERGY_POTCAR_CONFLICT')
            datasets[element] = definition
        by_element = metadata.get('u_by_element') or {}
        if method == 'PBE+U' and not by_element:
            fail('DFT+U 缺少元素映射定义', 'ENERGY_U_REQUIRED')
        for element in effective(sample)['composition']:
            definition = by_element.get(element)
            if definition is None and method and method != 'unknown' and method != 'PBE+U':
                definition = {'l': -1, 'u': 0.0, 'j': 0.0}
            if definition is None:
                warnings.append(sample['name'] + '：' + element + ' 的 U 定义未知')
                continue
            definition = ({'u': 0.0, 'j': 0.0} if definition.get('u') == 0 and definition.get('j') == 0 else
                          {**definition, 'type': parameters.get('LDAUTYPE', 2)})
            if element in u_values and u_values[element] != definition:
                fail('同元素 DFT+U 定义冲突：' + element, 'ENERGY_U_CONFLICT')
            u_values[element] = definition
        # Precision and smearing differences are checks, not universal method conflicts.
    if len(methods) > 1:
        fail('组内方法或泛函存在已知冲突', 'ENERGY_METHOD_CONFLICT')
    for key in ('ENCUT', 'ISMEAR', 'SIGMA', 'ISPIN'):
        values = {str((s['parsed'].get('metadata') or {}).get('parameters', {}).get(key)) for s in samples}
        if len(values) > 1:
            warnings.append(key + ' 不一致；需核对参考态与精度适用性')
    warnings.append('同名能量字段不证明参考态、磁态或数值精度已验证')
    return list(dict.fromkeys(warnings))


def calculate(doc):
    if not doc['groups']:
        fail('请建立比较组并确认参考', 'ENERGY_GROUP_REQUIRED')
    by_id = {s['id']: s for s in doc['samples']}
    output = []
    for group in doc['groups']:
        if not group['targets']:
            fail('比较组至少需要一个明确目标样本', 'ENERGY_TARGET_REQUIRED')
        if not group['basis_confirmed']:
            fail('请确认组级能量字段', 'ENERGY_BASIS_CONFIRMATION_REQUIRED', 409)
        basis, warnings, used = group['energy_basis'], [], {}

        def get(ident, role):
            if ident not in by_id:
                fail('必需参考或目标样本不存在', 'ENERGY_REFERENCE_REQUIRED')
            sample = by_id[ident]
            eff, risk = validate_source(sample, basis, doc['groups'], role)
            used[ident] = sample
            warnings.extend(risk)
            return sample, eff['composition'], eff['energy_fields'][basis], eff

        rows = []
        if group['kind'] == 'adsorption':
            clean, clean_comp, clean_energy, _ = get(group.get('clean_sample_id'), 'clean_slab')
            reference, ref_comp, ref_energy, _ = get(group.get('adsorbate_sample_id'), 'adsorbate')
            m = group['reference_units']
            if any(count % m for count in ref_comp.values()):
                fail('吸附物参考组成不能整除已确认参考单元数', 'ENERGY_STOICHIOMETRY_CONFLICT')
            unit_comp = {element: count // m for element, count in ref_comp.items()}
            for target in group['targets']:
                sample, comp, energy, eff = get(target['sample_id'], 'adsorbed')
                n = target['adsorbate_count']
                expected = dict(clean_comp)
                for element, count in unit_comp.items():
                    expected[element] = expected.get(element, 0) + n * count
                if comp != expected:
                    fail(sample['name'] + ' 与清洁表面和吸附物计量不守恒', 'ENERGY_STOICHIOMETRY_CONFLICT')
                delta = energy - clean_energy - n * ref_energy / m
                rows.append({'sample_id': sample['id'], 'name': sample['name'], 'delta_ev': delta,
                             'normalized_ev': delta / n, 'normalization': 'per_adsorbate', 'unit': 'eV/adsorbate',
                             'formula': 'E_target - E_clean - n * E_reference / m', 'adsorbate_count': n,
                             'reference_units': m, 'composition': comp, 'effective': eff,
                             'terms': [term(sample, energy, 1), term(clean, clean_energy, -1),
                                       term(reference, ref_energy, -n / m)], 'warnings': []})
            if len({t['adsorbate_count'] for t in group['targets']}) > 1:
                warnings.append('吸附物数量不同，覆盖度条件不同，不能视为同条件位点排名')
            warnings.append('负吸附能仅表示相对于所选参考能量降低，不证明全局稳定')
        else:
            refs = group.get('element_references') or {}
            for target in group['targets']:
                sample, comp, energy, eff = get(target['sample_id'], 'material')
                terms, delta = [term(sample, energy, 1)], energy
                if not set(comp) <= set(refs):
                    fail('每个目标元素必须有且仅有一个显式元素参考', 'ENERGY_REFERENCE_REQUIRED')
                for element, count in comp.items():
                    reference, ref_comp, ref_energy, _ = get(refs[element], 'element_reference')
                    if set(ref_comp) != {element}:
                        fail('元素参考必须是对应单元素组成：' + element, 'ENERGY_STOICHIOMETRY_CONFLICT')
                    coefficient = -count / ref_comp[element]
                    delta += coefficient * ref_energy
                    terms.append({**term(reference, ref_energy, coefficient), 'element': element,
                                  'target_atom_count': count, 'reference_atom_count': ref_comp[element],
                                  'mu_ev_per_atom': ref_energy / ref_comp[element]})
                rows.append({'sample_id': sample['id'], 'name': sample['name'], 'delta_ev': delta,
                             'normalized_ev': delta / sum(comp.values()), 'normalization': 'per_atom', 'unit': 'eV/atom',
                             'formula': 'E_target - sum(N_element * E_reference / N_reference)',
                             'composition': comp, 'effective': eff, 'terms': terms, 'warnings': []})
            warnings.append('元素参考由用户指定；负形成能不证明相对于所有竞争相稳定')
        warnings.extend(comparability(list(used.values())))
        warnings = list(dict.fromkeys(warnings))
        for row in rows:
            if not finite(row['delta_ev']) or not finite(row['normalized_ev']):
                fail('计算产生非有限数值', 'ENERGY_NONFINITE')
            row['warnings'] = warnings
        output.append({'id': group['id'], 'kind': group['kind'], 'name': group['name'], 'energy_basis': basis,
                       'reference_note': group.get('reference_note', ''), 'rows': rows, 'warnings': warnings})
    return {'schema_version': 'pp.energy.result.v1', 'input_fingerprint': input_fingerprint(doc),
            'groups': output, 'warnings': list(dict.fromkeys(w for g in output for w in g['warnings']))}


def term(sample, energy, coefficient):
    return {'sample_id': sample['id'], 'name': sample['name'], 'coefficient': coefficient,
            'energy_ev': energy, 'contribution_ev': coefficient * energy,
            'source_sha256': sample['source']['sha256'], 'status': sample['parsed']['status']}


def input_fingerprint(doc):
    return fingerprint({'samples': doc['samples'], 'groups': doc['groups']})
