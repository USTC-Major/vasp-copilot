"""Independent calculation cards over one shared, immutable-source sample pool.

The legacy formula engine remains the single numerical implementation. Card
review replaces its row-role confirmations; it does not relax scientific checks.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import uuid

from ...contracts import ToolboxError
from .calculation import (calculate, composition, confirmation, effective, finite,
                          fingerprint, input_fingerprint, reference_ids,
                          status_warnings, warning_acceptance)
from .errors import EnergyError, located
from .schemas import Group

VERSION = 'pp.energy.v2'
CONFIG = ('id', 'name', 'kind', 'energy_basis', 'basis_confirmed', 'clean_sample_id',
          'adsorbate_sample_id', 'reference_units', 'element_references', 'targets',
          'reference_note', 'reference_origins')
SCIENCE = ('kind', 'energy_basis', 'clean_sample_id', 'adsorbate_sample_id',
           'reference_units', 'element_references', 'targets', 'reference_note')


def sample_science(sample):
    override = sample.get('override') or {}
    return {'source': sample['source'], 'parsed': sample['parsed'],
            'energy_basis': sample.get('energy_basis'),
            'override': {key: override.get(key) for key in
                         ('composition', 'energy_fields', 'energy_basis', 'unit')}}


def used_ids(card):
    return reference_ids(card) | {target['sample_id'] for target in card['targets']}


def card_fingerprint(card, samples):
    by_id = {sample['id']: sample for sample in samples}
    return fingerprint({'card': {key: card.get(key) for key in SCIENCE},
                        'samples': {sid: sample_science(by_id[sid]) if sid in by_id else None
                                    for sid in sorted(used_ids(card))}})


def risk_fingerprint(card, samples):
    by_id = {sample['id']: sample for sample in samples}
    return fingerprint({sid: warning_acceptance(by_id[sid]) if sid in by_id else None
                        for sid in sorted(used_ids(card))})


def invalidate(card):
    card.update(locked=False, confirmed=False, confirmation_fingerprint=None,
                risk_accepted=False, risk_acceptance_fingerprint=None,
                basis_confirmed=False, result=None, status='stale')


def fresh(config):
    card = copy.deepcopy(config)
    card.update(locked=False, confirmed=False, basis_confirmed=False, confirmation_fingerprint=None,
                risk_accepted=False, risk_acceptance_fingerprint=None,
                result=None, status='draft', risks=[])
    return card


class EnergyCards:
    def __init__(self, store):
        self.store = store
        self.previews = {}

    def project(self, doc):
        """Read projection only; never migrate or write a legacy file here."""
        if doc.get('workflow') == 'cards':
            for card in doc['groups']:
                self.decorate(card, doc['samples'])
            return doc
        kinds = {group['kind'] for group in doc['groups']}
        mixed = len(kinds) > 1 or (not kinds and doc.get('analysis_kind') is None)
        doc['card_migration'] = {'required': True, 'read_only': mixed,
                                 'reason': 'mixed_types' if len(kinds) > 1 else 'type_required' if mixed else None}
        projected = []
        by_id = {sample['id']: sample for sample in doc['samples']}
        old_current = bool(doc.get('result') and doc['result'].get('input_fingerprint') == input_fingerprint(doc))
        old_lock = bool(doc.get('locked') and doc.get('lock_fingerprint') == input_fingerprint(doc))
        for group in doc['groups']:
            card = fresh(group)
            used = [by_id[sid] for sid in used_ids(group) if sid in by_id]
            verified = bool(group['targets'] and group.get('basis_confirmed') and
                            len(used) == len(used_ids(group)) and all(
                                sample.get('confirmed') and sample.get('confirmation_fingerprint') ==
                                confirmation(sample, doc['groups'], doc['samples']) for sample in used))
            risks_verified = all(not status_warnings(sample) or (sample.get('accepted_warnings') and
                                 sample.get('warning_acceptance_fingerprint') == warning_acceptance(sample))
                                 for sample in used)
            # A valid legacy result is tied to the entire old input; verify that
            # equality before translating its evidence to one card's dependency hash.
            if verified and risks_verified:
                card.update(confirmed=True, basis_confirmed=True, confirmation_fingerprint=card_fingerprint(card, doc['samples']),
                            risk_accepted=True, risk_acceptance_fingerprint=risk_fingerprint(card, doc['samples']),
                            locked=old_lock)
                result_group = next((row for row in (doc.get('result') or {}).get('groups', [])
                                     if row['id'] == group['id']), None)
                if old_current and result_group:
                    card['result'] = {**copy.deepcopy(doc['result']), 'groups': [copy.deepcopy(result_group)],
                                      'input_fingerprint': card_fingerprint(card, doc['samples']),
                                      'warnings': copy.deepcopy(result_group['warnings'])}
            elif group['targets']:
                card['status'] = 'stale'
            self.decorate(card, doc['samples'])
            projected.append(card)
        doc['card_projection'] = projected
        return doc

    @staticmethod
    def decorate(card, samples):
        current = card_fingerprint(card, samples)
        valid = bool(card.get('confirmed') and card.get('confirmation_fingerprint') == current and
                     card.get('risk_acceptance_fingerprint') == risk_fingerprint(card, samples))
        result = card.get('result')
        result_valid = bool(valid and result and result.get('input_fingerprint') == current)
        stale = (card.get('status') == 'stale' or (card.get('confirmed') and not valid)
                 or (result is not None and not result_valid))
        card['status'] = ('stale' if stale else 'result' if result_valid else
                          'locked' if card.get('locked') and valid else 'draft')
        card['risks'] = [{'sample_id': sample['id'], 'name': sample['name'], 'warnings': status_warnings(sample)}
                         for sample in samples if sample['id'] in used_ids(card) and status_warnings(sample)]
        if result:
            by_id = {sample['id']: sample for sample in samples}
            for group in result['groups']:
                group['name'] = card['name']
                group['reference_note'] = card.get('reference_note', '')
                for row in group['rows']:
                    if row['sample_id'] in by_id:
                        row['name'] = by_id[row['sample_id']]['name']
                    for term in row['terms']:
                        if term['sample_id'] in by_id:
                            term['name'] = by_id[term['sample_id']]['name']

    def editable_doc(self, ident, revision):
        doc = self.store.read(ident)
        self.store.expect(doc, revision)
        if doc.get('workflow') == 'cards':
            return doc
        if doc['card_migration']['read_only']:
            raise EnergyError('ENERGY_LEGACY_READ_ONLY', '旧混合或未定类型记录只读，请按明确类型复制', 409)
        upgraded = copy.deepcopy(doc)
        upgraded.update(schema_version=VERSION, workflow='cards', legacy_mode=False,
                        locked=False, lock_fingerprint=None, result=None,
                        groups=copy.deepcopy(doc['card_projection']), assignment_report=None)
        upgraded.pop('card_projection', None)
        upgraded.pop('card_migration', None)
        return upgraded

    @staticmethod
    def find(doc, card_id):
        card = next((card for card in doc['groups'] if card['id'] == card_id), None)
        if card is None:
            raise EnergyError('ENERGY_CARD_NOT_FOUND', '计算卡不存在', 404, card_id=card_id)
        return card

    @staticmethod
    def validate_config(doc, config):
        if config['kind'] != doc['analysis_kind']:
            raise EnergyError('ENERGY_ANALYSIS_KIND_CONFLICT', '同一分析只能包含所选分析类型', card_id=config['id'], field='kind')
        # An unselected HTML control submits ""; this is an incomplete draft,
        # not a reference to an unknown sample. Completeness is checked at lock.
        for key in ('clean_sample_id', 'adsorbate_sample_id'):
            config[key] = config.get(key) or None
        config['element_references'] = {element: sid for element, sid in
                                        config.get('element_references', {}).items() if sid}
        ids = [target['sample_id'] for target in config['targets']]
        if len(ids) != len(set(ids)):
            raise EnergyError('ENERGY_GROUP_INVALID', '同卡目标样本不能重复', card_id=config['id'], field='targets')
        known = {sample['id'] for sample in doc['samples']}
        missing = used_ids(config) - known
        if missing:
            sid = sorted(missing)[0]
            field = 'targets'
            for key in ('clean_sample_id', 'adsorbate_sample_id'):
                if config.get(key) == sid:
                    field = key
                    break
            else:
                for element, reference in config.get('element_references', {}).items():
                    if reference == sid:
                        field = 'element_references.' + element
                        break
            raise EnergyError('ENERGY_SAMPLE_INVALID', '引用样本不存在', card_id=config['id'], sample_id=sid, field=field)

    def create(self, ident, revision, config):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            self.validate_config(doc, config)
            if len(doc['groups']) >= 100 or any(card['id'] == config['id'] for card in doc['groups']):
                raise EnergyError('ENERGY_GROUP_INVALID', '计算卡ID重复或超过100张上限', card_id=config['id'])
            origins = config.setdefault('reference_origins', {})
            for key in ('clean_sample_id', 'adsorbate_sample_id'):
                if config.get(key):
                    origins.setdefault(key, 'manual')
            for element in config.get('element_references', {}):
                origins.setdefault('element:' + element, 'manual')
            for target in config['targets']:
                origins.setdefault('target:' + target['sample_id'], 'manual')
            if config['targets']:
                origins.setdefault('targets', 'manual')
            if config.get('reference_units', 1) != 1:
                origins.setdefault('reference_units', 'manual')
            doc['groups'].append(fresh(config))
            return self.store.save(doc)

    def configure(self, ident, revision, card_id, config):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            card = self.find(doc, card_id)
            if config['id'] != card_id:
                raise EnergyError('ENERGY_GROUP_INVALID', '卡ID与路由不一致', card_id=card_id, field='id')
            self.validate_config(doc, config)
            changed = card_fingerprint(card, doc['samples']) != card_fingerprint(config, doc['samples'])
            if changed and card.get('locked'):
                raise EnergyError('ENERGY_CARD_LOCKED', '计算卡已锁定，请先解锁编辑', 409, card_id=card_id)
            origins = dict(card.get('reference_origins') or {})
            origins.update(config.get('reference_origins') or {})
            for key in ('clean_sample_id', 'adsorbate_sample_id', 'reference_units'):
                if config.get(key) != card.get(key):
                    origins[key] = 'manual'
            for element in set(config.get('element_references') or {}) | set(card.get('element_references') or {}):
                if config.get('element_references', {}).get(element) != card.get('element_references', {}).get(element):
                    origins['element:' + element] = 'manual'
            previous = {target['sample_id']: target for target in card['targets']}
            if config['targets'] != card['targets']:
                origins['targets'] = 'manual'
            for target in config['targets']:
                if target != previous.get(target['sample_id']):
                    origins['target:' + target['sample_id']] = 'manual'
            basis_confirmed = card.get('basis_confirmed', False)
            card.update(copy.deepcopy(config), reference_origins=origins, basis_confirmed=basis_confirmed)
            if changed:
                invalidate(card)
            return self.store.save(doc)

    def copy(self, ident, revision, card_id, name=None):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            card = self.find(doc, card_id)
            if len(doc['groups']) >= 100:
                raise EnergyError('ENERGY_GROUP_INVALID', '最多100张计算卡', card_id=card_id)
            config = {key: copy.deepcopy(card.get(key)) for key in CONFIG}
            config.update(id='card_' + uuid.uuid4().hex, name=name or card['name'][:117] + ' 副本', basis_confirmed=False)
            doc['groups'].append(fresh(config))
            return self.store.save(doc)

    def delete(self, ident, revision, card_id):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            self.find(doc, card_id)
            doc['groups'] = [card for card in doc['groups'] if card['id'] != card_id]
            return self.store.save(doc)

    def engine(self, doc, card, accepted_warnings):
        work = {'workflow': 'cards', 'analysis_kind': doc['analysis_kind'],
                'samples': doc['samples'], 'groups': [{**card, 'risk_accepted': accepted_warnings}]}
        try:
            self.store.verify_sources({**doc, 'samples': [sample for sample in doc['samples'] if sample['id'] in used_ids(card)]})
            result = calculate(work)
        except ToolboxError as exc:
            raise located(exc, card['id']) from exc
        result['input_fingerprint'] = card_fingerprint(card, doc['samples'])
        return result

    def lock(self, ident, revision, card_id, accepted_warnings=False):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            card = self.find(doc, card_id)
            self.engine(doc, card, accepted_warnings)
            if card.get('result') and card['result'].get('input_fingerprint') != card_fingerprint(card, doc['samples']):
                card['result'] = None
            card.update(locked=True, confirmed=True, basis_confirmed=True,
                        confirmation_fingerprint=card_fingerprint(card, doc['samples']),
                        risk_accepted=accepted_warnings,
                        risk_acceptance_fingerprint=risk_fingerprint(card, doc['samples']), status='locked')
            return self.store.save(doc)

    def unlock(self, ident, revision, card_id):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            card = self.find(doc, card_id)
            card['locked'] = False
            return self.store.save(doc)

    @staticmethod
    def evidence(doc, card, require_lock=False):
        if require_lock and not card.get('locked'):
            raise EnergyError('ENERGY_CARD_LOCK_REQUIRED', '请确认并锁定当前计算卡后计算', 409, card_id=card['id'])
        if (not card.get('confirmed') or card.get('confirmation_fingerprint') != card_fingerprint(card, doc['samples'])
                or card.get('risk_acceptance_fingerprint') != risk_fingerprint(card, doc['samples'])):
            raise EnergyError('ENERGY_CARD_CONFIRMATION_STALE', '当前卡科学输入或风险依据已改变，请重新确认', 409, card_id=card['id'])

    def calculate(self, ident, revision, card_id):
        from .store import now
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            card = self.find(doc, card_id)
            self.evidence(doc, card, require_lock=True)
            card['result'] = {**self.engine(doc, card, card['risk_accepted']), 'calculated_at': now()}
            card['status'] = 'result'
            return self.store.save(doc)

    def autofill(self, ident, revision, card_id):
        from .assignment import plan
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            card = self.find(doc, card_id)
            if card.get('locked'):
                raise EnergyError('ENERGY_CARD_LOCKED', '计算卡已锁定，请先解锁编辑', 409, card_id=card_id)
            samples = copy.deepcopy(doc['samples'])
            # Roles are candidate hints inside the existing rule engine, never
            # the authoritative membership of the shared sample pool.
            for sample in samples:
                sample.update(role=None, included=False, role_origin=None, included_origin=None,
                              confirmed=False, accepted_warnings=False)
            config = Group.model_validate({key: card.get(key) for key in CONFIG}).model_dump()
            planned = plan({**doc, 'samples': samples, 'groups': [config]})
            suggested = planned['groups'][0]
            if card.get('reference_origins', {}).get('targets') == 'manual' or any(
                    value == 'manual' and key.startswith('target:') for key, value in card.get('reference_origins', {}).items()):
                suggested['targets'] = copy.deepcopy(card['targets'])
            before = card_fingerprint(card, doc['samples'])
            card.update(suggested)
            if card_fingerprint(card, doc['samples']) != before:
                invalidate(card)
            card['assignment_report'] = planned['assignment_report']
            return self.store.save(doc)

    def patched(self, doc, patches, title=None):
        from .store import name_valid
        updated = copy.deepcopy(doc)
        by_id = {sample['id']: sample for sample in updated['samples']}
        ids = [patch['sample_id'] for patch in patches]
        if len(ids) != len(set(ids)) or set(ids) - set(by_id):
            raise EnergyError('ENERGY_SAMPLE_INVALID', '样本不存在或重复', field='samples')
        for patch in patches:
            sample = by_id[patch['sample_id']]
            before = copy.deepcopy(sample)
            if patch.get('name') is not None:
                sample['name'] = name_valid(patch['name'])
            if 'override' in patch:
                override = patch['override']
                if override:
                    try:
                        if override.get('composition') is not None:
                            composition(override['composition'])
                        if override.get('energy_fields') is not None and (not override.get('energy_basis') or
                                not finite(override['energy_fields'].get(override['energy_basis']))):
                            raise EnergyError('ENERGY_FIELD_REQUIRED', '人工覆盖必须声明一个有值能量口径')
                    except ToolboxError as exc:
                        raise located(exc, sample_id=sample['id'], field='samples.' + sample['id'] + '.override.' +
                                      ('composition' if exc.code == 'ENERGY_COMPOSITION_REQUIRED' else 'energy_fields')) from exc
                sample['override'] = copy.deepcopy(override)
            if sample != before:
                sample['revision'] += 1
        if title is not None:
            updated['title'] = title.strip() or '能量比较'
        changed = [sample['id'] for sample in updated['samples'] if sample_science(sample) !=
                   sample_science(next(old for old in doc['samples'] if old['id'] == sample['id']))]
        return updated, changed

    def impact(self, doc, sample_ids, operation):
        cards = [card for card in doc['groups'] if set(sample_ids) & used_ids(card)]
        # Store a server-issued token: a guessed deterministic fingerprint cannot
        # serve as evidence that a client actually fetched the impact preview.
        token = 'impact_' + uuid.uuid4().hex
        value = {'preview_id': token, 'expected_revision': doc['revision'], 'sample_ids': list(sample_ids),
                 'affected_cards': [{'card_id': card['id'], 'name': card['name'], 'locked': card.get('locked', False)} for card in cards],
                 'requires_acknowledgement': any(card.get('locked') for card in cards)}
        self.previews[token] = {'collection_id': doc['id'], 'revision': doc['revision'],
                                'operation': fingerprint(operation), 'impact': copy.deepcopy(value)}
        if len(self.previews) > 256:
            self.previews.pop(next(iter(self.previews)))
        return value

    def acknowledge(self, doc, sample_ids, operation, preview_id, acknowledge):
        cards = [card for card in doc['groups'] if set(sample_ids) & used_ids(card)]
        if not any(card.get('locked') for card in cards):
            return
        if not preview_id or not acknowledge:
            raise EnergyError('ENERGY_IMPACT_ACK_REQUIRED', '共享修改影响已锁定卡，请先预览并明确确认影响', 409, field='preview_id')
        preview = self.previews.get(preview_id)
        if (not preview or preview['collection_id'] != doc['id'] or preview['revision'] != doc['revision']
                or preview['operation'] != fingerprint(operation)):
            raise EnergyError('ENERGY_IMPACT_PREVIEW_INVALID', '影响预览已过期或与提交内容不同，请重新预览', 409, field='preview_id')

    def change_preview(self, ident, revision, patches, title=None):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            _, changed = self.patched(doc, patches, title)
            return self.impact(doc, changed, {'samples': patches, 'title': title})

    def configure_samples(self, ident, revision, patches, title=None, preview_id=None, acknowledge=False):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            updated, changed = self.patched(doc, patches, title)
            self.acknowledge(doc, changed, {'samples': patches, 'title': title}, preview_id, acknowledge)
            for card in updated['groups']:
                if set(changed) & used_ids(card):
                    invalidate(card)
            return self.store.save(updated)

    def removal_preview(self, ident, revision, sample_ids, clear_all=False):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            removal = self.store.removal(doc, sample_ids, clear_all)
            removal['impact'] = self.impact(doc, removal['sample_ids'], {'remove': sorted(removal['sample_ids']), 'clear_all': clear_all})
            return removal

    def remove_samples(self, ident, revision, sample_ids, clear_all=False, preview_id=None, acknowledge=False):
        with self.store.guard:
            doc = self.editable_doc(ident, revision)
            removal = self.store.removal(doc, sample_ids, clear_all)
            selected = set(removal['sample_ids'])
            self.acknowledge(doc, selected, {'remove': sorted(selected), 'clear_all': clear_all}, preview_id, acknowledge)
            for card in doc['groups']:
                if not selected & used_ids(card):
                    continue
                invalidate(card)
                for key in ('clean_sample_id', 'adsorbate_sample_id'):
                    if card.get(key) in selected:
                        card[key] = None
                card['element_references'] = {element: sid for element, sid in card.get('element_references', {}).items() if sid not in selected}
                card['targets'] = [target for target in card['targets'] if target['sample_id'] not in selected]
            doc['samples'] = [sample for sample in doc['samples'] if sample['id'] not in selected]
            # Samples disappear from metadata atomically; their immutable local
            # source files remain available for recovering the original migration.
            return self.store.save(doc), removal

    def export(self, ident, card_id):
        with self.store.guard:
            doc = self.store.read(ident)
            if doc.get('workflow') != 'cards':
                if doc['card_migration']['read_only']:
                    raise EnergyError('ENERGY_LEGACY_READ_ONLY', '旧混合记录请按类型复制后导出卡结果', 409, card_id=card_id)
                doc = {**doc, 'workflow': 'cards', 'groups': doc['card_projection']}
            card = self.find(doc, card_id)
            self.evidence(doc, card)
            if not card.get('result') or card['result'].get('input_fingerprint') != card_fingerprint(card, doc['samples']):
                raise EnergyError('ENERGY_RESULT_STALE', '当前卡尚无有效结果，请重新计算', 409, card_id=card_id)
            samples = [sample for sample in doc['samples'] if sample['id'] in used_ids(card)]
            self.store.verify_sources({**doc, 'samples': samples})
            def scrub(value):
                if isinstance(value, dict):
                    return {key: scrub(item) for key, item in value.items() if key not in
                            {'remote_directory', 'remote_path', 'identity_file', 'known_hosts_path', 'card_projection', 'card_migration'}}
                if isinstance(value, list):
                    return [scrub(item) for item in value]
                return value
            return scrub({**doc, 'samples': samples, 'groups': [card], 'result': card['result']})

    def csv(self, ident, card_id):
        doc = self.export(ident, card_id)
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['record_type', 'card_id', 'sample_id', 'name', 'energy_basis', 'delta_ev',
                         'normalized_ev', 'unit', 'source_json', 'parsed_json', 'override_json', 'details_json', 'warnings_json'])
        dump = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False)
        text = self.store.csv_text
        for sample in doc['samples']:
            writer.writerow(['sample', card_id, sample['id'], text(sample['name']), sample.get('energy_basis') or '',
                             '', '', 'eV', dump(sample['source']), dump(sample['parsed']), dump(sample.get('override')),
                             '', dump(sample['parsed']['warnings'])])
        for group in doc['result']['groups']:
            for row in group['rows']:
                writer.writerow(['result', card_id, row['sample_id'], text(row['name']), group['energy_basis'],
                                 row['delta_ev'], row['normalized_ev'], row['unit'], '', '', '',
                                 dump({'group': group, 'row': row}), dump(row['warnings'])])
        return out.getvalue()
