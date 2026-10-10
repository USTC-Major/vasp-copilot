"""Finite, deterministic role candidates; never scientific or risk confirmation."""
from __future__ import annotations

import copy
import re

from ...contracts import ToolboxError
from .calculation import composition, effective
from .schemas import Group


def role_hints(sample, kind):
    label = ' '.join((sample['name'], sample['source'].get('relative_path') or '')).lower()
    patterns = ({'clean_slab': r'clean|bare|清洁|裸表面', 'adsorbate': r'adsorbate|molecule|gas|分子|气相',
                 'adsorbed': r'adsorbed|slab[+_ -]+ads|吸附构型|吸附态'} if kind == 'adsorption' else
                {'material': r'material|compound|材料|化合物', 'element_reference': r'element|reference|单质|元素参考'})
    return {role for role, pattern in patterns.items() if re.search(pattern, label)}


def plan(doc):
    """Re-evaluate the full batch; old auto choices never constrain new evidence."""
    kind = doc['analysis_kind']
    original = {sample['id']: sample for sample in doc['samples']}
    rows = {}
    issues = []
    comps, hints = {}, {}

    def issue(code, message, ids):
        issues.append({'code': code, 'message': message, 'sample_ids': sorted(set(ids))})

    for sample in doc['samples']:
        sid = sample['id']
        row = {'sample_id': sid, **{key: copy.deepcopy(sample.get(key)) for key in
               ('name', 'role', 'included', 'confirmed', 'accepted_warnings', 'override', 'role_origin', 'included_origin')}}
        row['assignment_reasons'] = []
        if row['role_origin'] == 'auto':
            row['role'] = None
        if row['included_origin'] == 'auto':
            row['included'] = False
        rows[sid] = row
        if sample.get('included_origin') == 'manual' and not sample['included']:
            row['assignment_reasons'] = ['用户已明确排除，自动分配保留该选择']
            continue
        if sample.get('role_origin') == 'manual' and sample['role'] is None:
            row['assignment_reasons'] = ['用户已清空角色，等待人工指定']
            continue
        parsed = sample['parsed']
        repairable = {entry.get('code') for entry in parsed.get('issues', [])
                      if entry.get('recoverable_by_manual') and (sample.get('override') or {}).get('composition')}
        hard = [entry for entry in parsed.get('issues', []) if entry.get('severity') == 'error' and entry.get('code') not in repairable]
        reasons = ((parsed.get('metadata') or {}).get('support') or {}).get('reasons') or []
        parameters = (parsed.get('metadata') or {}).get('parameters') or {}
        if hard or any(reason not in repairable for reason in reasons) or (parsed.get('errors') and not parsed.get('issues')) or any(
                parameters.get(key) is True for key in ('LSORBIT', 'LNONCOLLINEAR')):
            issue('SOURCE_HARD_ERROR', '来源存在硬错误或不支持的方法证据，保持待处理', [sid])
            continue
        try:
            comps[sid] = composition(effective(sample)['composition'])
        except ToolboxError:
            issue('COMPOSITION_REQUIRED', '缺少有效的元素正整数计数，不能自动确定关系', [sid])
            continue
        hints[sid] = role_hints(sample, kind)
        if len(hints[sid]) > 1:
            issue('ROLE_EVIDENCE_CONFLICT', '名称或目录含冲突角色线索，请人工核查', [sid])
            comps.pop(sid)

    groups = copy.deepcopy(doc['groups'])
    if not groups:
        groups = [Group(id='analysis', name='吸附能' if kind == 'adsorption' else '形成能', kind=kind,
                        energy_basis='sigma_to_zero_ev').model_dump()]
    group = groups[0]
    origins = group.setdefault('reference_origins', {})
    previous_targets = {target['sample_id']: target for target in group['targets']}
    group['targets'] = []

    def permitted(sid, role):
        row = rows[sid]
        if row['role_origin'] == 'manual':
            return row['role'] == role
        return not hints.get(sid) or hints[sid] == {role}

    def apply(sid, role, explanation):
        row = rows[sid]
        if row['role_origin'] != 'manual':
            row.update(role=role, role_origin='auto')
        if row['included_origin'] != 'manual':
            row.update(included=True, included_origin='auto')
        row['assignment_reasons'] = explanation + (['人工指定的角色已保留'] if row['role_origin'] == 'manual' else [])

    def bind(key, sid):
        if origins.get(key) != 'manual':
            origins[key] = 'auto'
            if key.startswith('element:'):
                group['element_references'][key.split(':', 1)[1]] = sid
            else:
                group[key] = sid

    def target(sid, count=1):
        former = previous_targets.get(sid)
        if former and origins.get('target:' + sid) == 'manual':
            group['targets'].append(former)
            if kind == 'adsorption' and former.get('adsorbate_count') != count:
                issue('MANUAL_COUNT_CONFLICT', '人工吸附数量与当前组成关系不同，保留人工值等待核对', [sid])
        else:
            group['targets'].append({'sample_id': sid, 'adsorbate_count': count})
            origins['target:' + sid] = 'auto'

    if kind == 'formation':
        targets = [sid for sid, comp in comps.items() if permitted(sid, 'material') and
                   (len(comp) > 1 or rows[sid]['role_origin'] == 'manual' or hints.get(sid) == {'material'})]
        required = {element for sid in targets for element in comps[sid]}
        group['element_references'] = {element: sid for element, sid in (group.get('element_references') or {}).items()
                                       if origins.get('element:' + element) == 'manual'}
        for element in sorted(required):
            key = 'element:' + element
            candidates = [sid for sid, comp in comps.items() if set(comp) == {element} and permitted(sid, 'element_reference')]
            if origins.get(key) == 'manual':
                selected = group['element_references'].get(element)
                if selected not in candidates:
                    issue('MANUAL_REFERENCE_REQUIRED', element + ' 的人工参考为空或与当前组成不符，保留待处理', [sid for sid in targets if element in comps[sid]])
                    continue
            elif len(candidates) == 1:
                selected = candidates[0]
                bind(key, selected)
            else:
                issue('REFERENCE_AMBIGUOUS' if len(candidates) > 1 else 'REFERENCE_REQUIRED',
                      element + (' 有多个元素参考候选，不按能量选择' if candidates else ' 缺少元素参考'), candidates or targets)
                continue
            apply(selected, 'element_reference', [element + ' 单元素组成及目标元素需求形成唯一参考候选；不证明参考态正确'])
        for sid in targets:
            apply(sid, 'material', ['有效多元素组成或人工目标定义符合形成能候选范围；参考态仍需核对'])
            target(sid)
        if not targets:
            issue('TARGET_REQUIRED', '未找到明确目标材料，请指定目标角色', list(comps))
    else:
        from pymatgen.core import Element
        host = {sid: any(Element(element).is_metal or Element(element).is_metalloid for element in comp)
                for sid, comp in comps.items()}
        fixed_clean = group.get('clean_sample_id') if origins.get('clean_sample_id') == 'manual' else None
        fixed_ref = group.get('adsorbate_sample_id') if origins.get('adsorbate_sample_id') == 'manual' else None
        clean_ids = [sid for sid in comps if permitted(sid, 'clean_slab') and
                     (rows[sid]['role_origin'] == 'manual' or hints.get(sid) == {'clean_slab'} or host[sid])]
        ref_ids = [sid for sid in comps if permitted(sid, 'adsorbate') and
                   (rows[sid]['role_origin'] == 'manual' or hints.get(sid) == {'adsorbate'} or not host[sid])]
        if origins.get('clean_sample_id') == 'manual':
            clean_ids = [fixed_clean] if fixed_clean in clean_ids else []
        if origins.get('adsorbate_sample_id') == 'manual':
            ref_ids = [fixed_ref] if fixed_ref in ref_ids else []
        m = group.get('reference_units', 1) if origins.get('reference_units') == 'manual' else 1
        candidates = []
        for clean in clean_ids:
            for reference in ref_ids:
                if clean == reference or any(count % m for count in comps[reference].values()):
                    continue
                unit = {element: count // m for element, count in comps[reference].items()}
                matched = []
                for sid, comp in comps.items():
                    if sid in {clean, reference} or not permitted(sid, 'adsorbed'):
                        continue
                    diff = {element: comp.get(element, 0) - comps[clean].get(element, 0) for element in set(comp) | set(comps[clean])}
                    if any(count < 0 for count in diff.values()) or any(count and element not in unit for element, count in diff.items()):
                        continue
                    ratios = {diff.get(element, 0) // count for element, count in unit.items()
                              if diff.get(element, 0) % count == 0}
                    if len(ratios) != 1 or any(diff.get(element, 0) % count for element, count in unit.items()):
                        continue
                    n = next(iter(ratios))
                    if n > 0:
                        matched.append((sid, n))
                if matched:
                    candidates.append((clean, reference, matched))
        if len(candidates) == 1:
            clean, reference, matched = candidates[0]
            explanation = ['名称/目录仅作线索；金属或类金属宿主与非金属参考的有限规则，加正整数计量关系形成唯一候选；不证明表面或参考态适用性']
            apply(clean, 'clean_slab', explanation)
            apply(reference, 'adsorbate', explanation)
            bind('clean_sample_id', clean)
            bind('adsorbate_sample_id', reference)
            if origins.get('reference_units') != 'manual':
                group['reference_units'] = 1
                origins['reference_units'] = 'auto'
            for sid, count in matched:
                apply(sid, 'adsorbed', [f'目标组成 = 表面组成 + {count} × 已定义参考单元；整份吸附物参考按 {m} 个已定义单元计，不约化分子组成'])
                target(sid, count)
        else:
            for key in ('clean_sample_id', 'adsorbate_sample_id'):
                if origins.get(key) != 'manual':
                    group[key] = None
            ids = [sid for clean, reference, matched in candidates for sid in [clean, reference, *[item[0] for item in matched]]]
            issue('RELATION_AMBIGUOUS' if candidates else 'RELATION_REQUIRED',
                  '存在多个可行表面/吸附物配对，不按能量或原子数选择' if candidates else
                  '缺少唯一的表面/吸附物正整数计量关系；全非金属或分子单元不明确时需人工指定', ids or list(comps))

    # Preserve relationships that the user explicitly defined, even while a missing
    # reference makes their calculation pending. Never replace a manual count.
    target_ids = {entry['sample_id'] for entry in group['targets']}
    for sid, former in previous_targets.items():
        row = rows.get(sid)
        if row and sid not in target_ids and row['included'] and (
                row['role_origin'] == 'manual' or row['included_origin'] == 'manual' or origins.get('target:' + sid) == 'manual'):
            group['targets'].append(former)
    used = {row['sample_id'] for row in rows.values() if row['included'] and row['role'] is not None}
    for sid, row in rows.items():
        if sid not in used and not row['assignment_reasons'] and sid in comps:
            row['assignment_reasons'] = ['当前证据未唯一确定角色/关系，等待人工核查']
            issue('ROLE_PENDING', '当前证据未唯一确定角色/关系，请指定或排除', [sid])
        was_auto = original[sid].get('role_origin') == 'auto' and original[sid]['role'] is not None
        if was_auto and row['role'] != original[sid]['role']:
            issue('AUTO_ASSIGNMENT_CONFLICT', '追加证据使此前自动角色不再唯一，请重新核查', [sid])
    ambiguous = any(entry['code'].endswith('AMBIGUOUS') or entry['code'] == 'AUTO_ASSIGNMENT_CONFLICT' for entry in issues)
    return {'expected_revision': doc['revision'], 'samples': list(rows.values()), 'groups': groups,
            'assignment_report': {'state': 'ambiguous' if ambiguous else 'pending' if issues else 'ready', 'issues': issues}}
