"""Reviewed, finite PAW-PBE advice. No release inference or scientific ranking."""
from __future__ import annotations

from .filesystem import fail

RULE_VERSION = 'paw-pbe-selection-r1'
SOURCES = (
    {'source_id': 'S1', 'title': 'VASP: Choosing pseudopotentials', 'url': 'https://vasp.at/wiki/index.php?title=Choosing_pseudopotentials&oldid=38073'},
    {'source_id': 'S2', 'title': 'VASP: Available pseudopotentials', 'url': 'https://vasp.at/wiki/index.php?title=Available_pseudopotentials&oldid=25069'},
    {'source_id': 'S3', 'title': 'VASP: Preparing a POTCAR', 'url': 'https://vasp.at/wiki/index.php?title=Preparing_a_POTCAR&oldid=25377'},
    {'source_id': 'S4', 'title': 'VASP: TITEL (dataset creation date)', 'url': 'https://vasp.at/wiki/index.php?title=TITEL&oldid=25191'},
    {'source_id': 'S5', 'title': 'VASP: POTCAR (read-only source)', 'url': 'https://vasp.at/wiki/index.php?title=POTCAR&oldid=33125'},
    {'source_id': 'S6', 'title': 'VASP: LDAUL', 'url': 'https://vasp.at/wiki/index.php?title=LDAUL&oldid=36501'},
    {'source_id': 'S7', 'title': 'VASP official magnetism tutorial (NiO)', 'url': 'https://vasp.at/tutorials/latest/magnetism/part1/'},
    {'source_id': 'S8', 'title': 'VASP workshop: The PAW and US-PP database, pp. 14, 18, 20, 23', 'url': 'https://www.vasp.at/vasp-workshop/pseudoppdatabase.pdf'},
)
CONDITIONS = ('spin_polarized', 'short_bonds', 'high_pressure', 'high_unoccupied', 'magnetic_energy')
RULES = (
    ('G-FIRSTROW-PLAIN', {e: e for e in ('B', 'C', 'N', 'O', 'F')}, ('S1',)),
    ('G-PBLOCK-D', {e: e + '_d' for e in ('Ga', 'Ge', 'In', 'Sn')}, ('S1', 'S8')),
    ('G-LI-SV', {'Li': 'Li_sv'}, ('S1', 'S8')),
    ('G-ALKALI-SV', {e: e + '_sv' for e in ('Rb', 'Sr', 'Cs', 'Ba')}, ('S1', 'S8')),
)
CONFLICT_ELEMENTS = {'Be', 'Mg', 'K', 'Ca', 'At'}
D_ELEMENTS = set('Sc Ti V Cr Mn Fe Co Ni Cu Zn Y Zr Nb Mo Tc Ru Rh Pd Ag Cd Hf Ta W Re Os Ir Pt Au Hg'.split())
F_ELEMENTS = set('La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr'.split())


def context(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {'purpose', 'functional', *CONDITIONS}:
        fail('INVALID_REQUEST', '计算用途包含未知字段')
    purpose, functional = value.get('purpose', 'unknown'), value.get('functional', 'unknown')
    if (not isinstance(purpose, str) or not isinstance(functional, str) or
            purpose not in {'unknown', 'regular', 'special'} or functional not in {'unknown', 'PBE', 'PBE+U', 'HSE06'}):
        fail('INVALID_REQUEST', '计算用途或泛函不受支持')
    result = {'purpose': purpose, 'functional': functional}
    for key in CONDITIONS:
        item = value.get(key)
        if item is not None and type(item) is not bool:
            fail('INVALID_REQUEST', '高级计算条件需为 true、false 或 null')
        result[key] = item
    return result


def advice(code, message, rules=(), sources=(), targets=()):
    return {'code': code, 'message': message, 'rule_ids': list(rules),
            'source_ids': list(sources), 'target_variants': list(targets)}


def choose(element, candidates, explicit, ctx):
    notes = [advice('RELEASE_UNKNOWN', '本库发布版本未知；元素通则不证明具体发布版适用性', sources=('S4', 'S2'))]
    regular = ctx['purpose'] == 'regular' and ctx['functional'] in {'PBE', 'PBE+U', 'HSE06'}
    special = ctx['purpose'] == 'special' or any(ctx[k] is True for k in ('short_bonds', 'high_pressure', 'high_unoccupied'))
    matching = [(rid, table[element], sources) for rid, table, sources in RULES if element in table]
    for rid, target, sources in matching:
        notes.append(advice('ELEMENT_GUIDANCE', '普通用途的元素通则建议 ' + target + '；请核对当前数据集与研究目标', (rid,), sources, (target,)))
    if not regular:
        notes.append(advice('CONTEXT_UNKNOWN' if ctx['purpose'] == 'unknown' or ctx['functional'] == 'unknown' else 'SPECIAL_PURPOSE',
                            '未明确普通用途及泛函，科学建议不作自动预选', sources=('S1', 'S3')))
    for condition, code, text, sources in (
        ('short_bonds', 'SHORT_BONDS_REVIEW', '短键／分子或高精度键长目标：可核对硬势候选，需人工选择与收敛测试', ('S1',)),
        ('high_pressure', 'PRESSURE_REVIEW', '高压／强压缩：需核对芯区重叠及半芯态，不存在统一后缀或压力阈值', ('S8',)),
        ('high_unoccupied', 'UNOCCUPIED_REVIEW', '高能未占据态目标：核对 _GW 候选，普通默认选择降级为人工处理', ('S3',)),
    ):
        if ctx[condition] is True:
            notes.append(advice(code, text, sources=sources,
                                targets=() if condition == 'high_pressure' else tuple(sorted({r['variant'] for r in candidates if r['variant'].endswith('_h' if condition == 'short_bonds' else '_GW')}))))
    if element in D_ELEMENTS:
        notes.append(advice('SEMICORE_REVIEW', '半芯态取舍需结合目标和对照测试；不按 _pv/_sv 或 ENMAX 排名', sources=('S1', 'S3', 'S7')))
    if ctx['spin_polarized'] is True or ctx['magnetic_energy'] is True:
        notes.append(advice('MAGNETIC_REVIEW', '自旋极化或磁性能量差可能对半芯态敏感；不据此自动升级变体', sources=('S3', 'S7')))
    if ctx['functional'] == 'HSE06':
        notes.append(advice('HYBRID_REVIEW', 'HSE06 使用 PAW-PBE 范围；不因杂化自动选择 _GW，d 区需核对半芯态', sources=('S3',)))
        if any(r['variant'].endswith('_s') for r in candidates):
            notes.append(advice('HYBRID_SOFT_AVOID', '强提示：官方建议杂化／HF 避免 _s 软势；手动选择仍需本次明确核对', sources=('S1',)))
    if ctx['functional'] == 'PBE+U':
        notes.append(advice('DFTU_CHANNEL_REVIEW', 'U/J 数组按当前物种顺序保持；l=2 不要求 _d，价态及投影子方案需人工核对', sources=('S6', 'S7')))
    if element in F_ELEMENTS or any(r['variant'].endswith(('_2', '_3', '_AE')) for r in candidates):
        notes.append(advice('SPECIAL_VARIANT_REVIEW', 'f 电子／固定价态等特殊方案需专业核对；不从化学式推断价态', sources=('S2',)))
        if element in F_ELEMENTS:
            special = True
    known = {'', '_d', '_pv', '_sv', '_h', '_s', '_GW', '_sv_GW', '_pv_GW', '_d_GW'}
    if any(r['variant'][len(element):] not in known for r in candidates):
        notes.append(advice('UNKNOWN_VARIANT', '存在未纳入首批通则的变体名称，请人工核对', sources=('S2',)))
    if element in CONFLICT_ELEMENTS:
        notes.append(advice('RULE_CONFLICT', '官方元素通则与指定发布版表有不同倾向；首期人工选择', sources=('S1', 'S2', 'S8')))
    active = matching if regular and not special else []
    targets = {target for _, target, _ in active}
    blocked_default = special or element in CONFLICT_ELEMENTS
    recommended = None
    if len(targets) > 1:
        notes.append(advice('RULE_CONFLICT', '适用规则指向不同目标；请人工核对', [r[0] for r in active], ('S1', 'S8'), sorted(targets)))
        blocked_default = True
    elif targets:
        target = next(iter(targets))
        found = [r for r in candidates if r['variant'] == target]
        if not found:
            notes.append(advice('RECOMMENDED_MISSING', '推荐目标 ' + target + ' 在当前索引缺失；请手选或补充来源后扫描', [r[0] for r in active], ('S1',), (target,)))
            blocked_default = True
        elif len(found) != 1 or found[0]['status'] != 'ready':
            notes.append(advice('RECOMMENDED_AMBIGUOUS', '推荐名称存在多个来源或不同内容；必须明确选择具体数据集', [r[0] for r in active], ('S1',), (target,)))
            blocked_default = True
        else:
            recommended = found[0]['dataset_id']
    if explicit is not None:
        return explicit, 'USER_SELECTED', '用户明确选择；独立建议仍需核对', notes
    if recommended:
        return recommended, 'RULE_RECOMMENDED', '按已审元素通则预选；发布版本未知，请核对', notes
    if len(candidates) == 1 and not blocked_default and candidates[0]['status'] == 'ready' and candidates[0]['variant'][len(element):] in known and not (
            ctx['functional'] == 'HSE06' and candidates[0]['variant'].endswith('_s')):
        return candidates[0]['dataset_id'], 'UNIQUE_COMPATIBLE', '仅有一个格式兼容候选；唯一可用不代表科学最优', notes
    if candidates:
        return None, 'SELECTION_REQUIRED', '请明确选择变体或来源项，并核对建议', notes
    return None, 'NO_COMPATIBLE_DATASET', '本库没有此元素的受支持单数据集候选；请更换库或补充来源后扫描', notes
