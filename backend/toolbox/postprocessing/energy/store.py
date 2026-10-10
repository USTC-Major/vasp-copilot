"""Atomic independent collections; immutable UUID source files and revisions."""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

from ..store import MAX_BATCH, MAX_FILE, MAX_STORE, atomic, fail
from ...contracts import ToolboxError
from .calculation import (FIELDS, calculate, composition, confirmation, effective, finite,
                          fingerprint, input_fingerprint, group_confirmation, groups_for,
                          legacy_confirmation, warning_acceptance)

MAX_SAMPLES = 100
LIMITS = {'max_file_bytes': MAX_FILE, 'max_collection_bytes': MAX_BATCH, 'max_samples': MAX_SAMPLES}


def now():
    return datetime.now(timezone.utc).isoformat()


def display_path(value):
    if value is None or value == '':
        return None
    if (not isinstance(value, str) or len(value) > 1024 or value.startswith(('/', '\\'))
            or re.match(r'^[A-Za-z]:', value) or '\\' in value
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        fail('相对目录仅接受不含父目录、绝对路径或控制字符的展示标签', 'ENERGY_RELATIVE_PATH_INVALID')
    return value


def name_valid(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
        fail('样本名称为空、过长或含控制字符', 'ENERGY_NAME_INVALID')
    return value


def suggestion(sample):
    label = ' '.join([sample['name'], sample['source'].get('relative_path') or '']).lower()
    choices = []
    if re.search(r'clean|bare|清洁|裸表面', label):
        choices.append(('clean_slab', '名称含清洁表面提示'))
    if re.search(r'adsorbed|slab[+_ -]+ads|吸附构型|吸附态', label):
        choices.append(('adsorbed', '名称含吸附构型提示'))
    if re.search(r'adsorbate|molecule|gas|分子|气相', label):
        choices.append(('adsorbate', '名称含吸附物或气相提示；需核对参考单元和字段'))
    if re.search(r'material|compound|材料|化合物', label):
        choices.append(('material', '名称含材料提示'))
    if re.search(r'element|reference|单质|元素参考', label) and len(sample['parsed'].get('composition') or {}) == 1:
        choices.append(('element_reference', '单元素组成及名称提示；不证明标准态正确'))
    return {'role': choices[0][0] if len(choices) == 1 else None,
            'reasons': [reason for _, reason in choices] or ['来源名称和组成不足以唯一确定角色，请手动指定'],
            'confidence': 'hint' if len(choices) == 1 else 'unknown'}


class EnergyStore:
    def __init__(self, root):
        self.root = Path(root) / 'postprocessing-energy'
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = threading.RLock()
        self.uploads = {}
        self.task_sources = None
        from .cards import EnergyCards
        self.cards = EnergyCards(self)
        # A previous process never publishes temporary sources as samples.
        for temp in self.root.glob('*.upload'):
            temp.unlink(missing_ok=True)

    def directory(self, ident):
        if not re.fullmatch(r'ec_[a-f0-9]{32}', ident):
            fail('能量比较集不存在', 'ENERGY_NOT_FOUND', 404)
        path = self.root / ident
        if not path.is_dir() or path.is_symlink():
            fail('能量比较集不存在', 'ENERGY_NOT_FOUND', 404)
        return path

    def read(self, ident):
        with self.guard:
            try:
                doc = json.loads((self.directory(ident) / 'metadata.json').read_text(encoding='utf-8'))
                original_input = input_fingerprint(doc)
                result_current = bool(doc.get('result') and doc['result'].get('input_fingerprint') == original_input)
                lock_current = bool(doc.get('locked') and doc.get('lock_fingerprint') == original_input)
                if 'legacy_mode' not in doc:
                    # Compatibility is a read projection; never rewrite an old record here.
                    kinds = {group['kind'] for group in doc['groups']}
                    doc.update(analysis_kind=next(iter(kinds)) if len(kinds) == 1 else None,
                               legacy_mode=True, locked=False, lock_fingerprint=None)
                    for sample in doc['samples']:
                        valid = sample.get('confirmation_fingerprint') == legacy_confirmation(sample, doc['groups'])
                        if sample['confirmed'] and valid:
                            sample['confirmation_fingerprint'] = confirmation(sample, doc['groups'], doc['samples'])
                        sample['warning_acceptance_fingerprint'] = (
                            warning_acceptance(sample) if valid and sample['accepted_warnings'] else None)
                doc.setdefault('assignment_report', None)
                for sample in doc['samples']:
                    sample.setdefault('role_origin', 'manual' if sample['role'] is not None else None)
                    sample.setdefault('included_origin', 'manual' if sample['role'] is not None or sample['included'] else None)
                    sample.setdefault('assignment_reasons', [])
                for group in doc['groups']:
                    if 'reference_origins' not in group:
                        # Records predating provenance already contain user-defined
                        # bindings/counts. Preserve them without reclassifying an explicit
                        # empty origins map on a newly created group.
                        origins = {key: 'manual' for key in ('clean_sample_id', 'adsorbate_sample_id')
                                   if group.get(key) is not None}
                        origins.update({'element:' + element: 'manual' for element, sid in
                                        (group.get('element_references') or {}).items() if sid is not None})
                        if group['kind'] == 'adsorption' and 'reference_units' in group:
                            origins['reference_units'] = 'manual'
                        origins.update({'target:' + target['sample_id']: 'manual' for target in group['targets']
                                        if 'adsorbate_count' in target})
                        group['reference_origins'] = origins
                if result_current:
                    doc['result']['input_fingerprint'] = input_fingerprint(doc)
                if lock_current:
                    doc['lock_fingerprint'] = input_fingerprint(doc)
                return self.cards.project(doc)
            except FileNotFoundError:
                fail('能量比较集不存在', 'ENERGY_NOT_FOUND', 404)

    def list(self):
        with self.guard:
            docs = []
            for directory in self.root.glob('ec_*'):
                try:
                    docs.append(self.read(directory.name))
                except (OSError, ValueError):
                    continue
            return sorted(docs, key=lambda doc: doc['updated_at'], reverse=True)

    def save(self, doc):
        path = self.directory(doc['id']) / 'metadata.json'
        if doc.get('workflow') == 'cards' and path.is_file():
            original = path.read_bytes()
            if json.loads(original).get('schema_version') != 'pp.energy.v2':
                backup = path.with_name('metadata.pp.energy.v1.backup.json')
                if not backup.exists():
                    # Preserve exact original bytes before the versioned atomic
                    # replacement. A failed replacement leaves the original live.
                    if self.committed_bytes() + len(original) + sum(self.uploads.values()) > MAX_STORE:
                        fail('本地空间不足以保留可恢复迁移原件', 'ENERGY_QUOTA', 413)
                    if shutil.disk_usage(self.root).free < len(original) + self.pending_disk_bytes():
                        fail('本地空间不足以保留可恢复迁移原件', 'ENERGY_DISK_FULL', 413)
                    temporary = backup.with_name(backup.name + '.' + uuid.uuid4().hex + '.tmp')
                    try:
                        with temporary.open('xb') as stream:
                            stream.write(original)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, backup)
                    finally:
                        temporary.unlink(missing_ok=True)
        doc.pop('card_projection', None)
        doc.pop('card_migration', None)
        doc['revision'] += 1
        doc['updated_at'] = now()
        self.write_json(path, doc)
        return self.cards.project(doc)

    def committed_bytes(self):
        return sum(p.stat().st_size for p in self.root.rglob('*')
                   if p.is_file() and not p.is_symlink() and p.suffix not in {'.upload', '.tmp'})

    def pending_disk_bytes(self):
        # Free disk already excludes staged bytes; reserve only their unwritten remainder.
        return sum(max(0, budget - (Path(path).stat().st_size if Path(path).is_file() else 0))
                   for path, budget in self.uploads.items())

    def write_json(self, path, value):
        with self.guard:
            size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))
            old_size = path.stat().st_size if path.is_file() else 0
            if self.committed_bytes() - old_size + size + sum(self.uploads.values()) > MAX_STORE:
                fail('本地能量缓存总额达到1 GiB上限', 'ENERGY_QUOTA', 413)
            if shutil.disk_usage(self.root).free < size + self.pending_disk_bytes():
                fail('本地空间不足以原子保存能量记录', 'ENERGY_DISK_FULL', 413)
            atomic(path, value)

    def create(self, title, analysis_kind=None, workflow=None):
        with self.guard:
            if workflow == 'cards' and analysis_kind is None:
                fail('新计算卡分析必须明确选择分析类型', 'ENERGY_ANALYSIS_KIND_REQUIRED')
            if len(self.list()) >= 100:
                fail('最多保存100个能量比较集', 'ENERGY_QUOTA', 413)
            ident = 'ec_' + uuid.uuid4().hex
            (self.root / ident).mkdir()
            doc = {'id': ident, 'schema_version': 'pp.energy.v1', 'title': title.strip() or '能量比较',
                   'revision': 0, 'created_at': now(), 'updated_at': now(), 'samples': [], 'groups': [],
                   'result': None, 'limits': LIMITS, 'analysis_kind': analysis_kind,
                   'legacy_mode': analysis_kind is None, 'locked': False, 'lock_fingerprint': None,
                   'assignment_report': None}
            if workflow == 'cards':
                doc.update(schema_version='pp.energy.v2', workflow='cards', legacy_mode=False)
            try:
                return self.save(doc)
            except BaseException:
                shutil.rmtree(self.root / ident)
                raise

    @staticmethod
    def expect(doc, revision):
        if revision != doc['revision']:
            fail('比较集已被修改，请重读后保存', 'ENERGY_REVISION_CONFLICT', 409)

    @staticmethod
    def editable(doc):
        if doc.get('workflow') == 'cards':
            return
        if (doc.get('card_migration') or {}).get('read_only') and doc['groups']:
            fail('旧混合类型分析只读，请按明确类型复制', 'ENERGY_LEGACY_READ_ONLY', 409)
        if doc.get('locked'):
            fail('分析已锁定，请先解锁编辑', 'ENERGY_LOCKED', 409)

    @staticmethod
    def legacy_api(doc):
        if doc.get('workflow') == 'cards':
            fail('多计算卡分析请使用指定卡接口', 'ENERGY_CARD_WORKFLOW_REQUIRED', 409)
        if (doc.get('card_migration') or {}).get('read_only') and doc['groups']:
            fail('旧混合类型分析只读，请按明确类型复制', 'ENERGY_LEGACY_READ_ONLY', 409)

    def check_editable(self, ident, revision):
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.editable(doc)
            return doc

    @staticmethod
    def valid_lock(doc):
        if not doc.get('legacy_mode') and not doc.get('locked'):
            fail('请核对并锁定分析后再计算或导出', 'ENERGY_LOCK_REQUIRED', 409)
        if doc.get('locked') and doc.get('lock_fingerprint') != input_fingerprint(doc):
            fail('锁定时的科学输入已改变，请解锁并重新核对', 'ENERGY_LOCK_STALE', 409)

    def capacity(self, doc, size, count=1, already_staged=0):
        if len(doc['samples']) + count > MAX_SAMPLES:
            fail('每个比较集最多100个样本', 'ENERGY_QUOTA', 413)
        raw_bytes = sum(s['source'].get('size_bytes', 0) for s in doc['samples'])
        if size > MAX_FILE or raw_bytes + size > MAX_BATCH:
            fail('单文件最多64 MiB，比较集原始文件最多128 MiB', 'ENERGY_TOO_LARGE', 413)
        # Energy's independent cache budget never counts or changes DOS/band data.
        used = self.committed_bytes()
        if used + size + sum(self.uploads.values()) > MAX_STORE:
            fail('本地能量缓存总额达到1 GiB上限', 'ENERGY_QUOTA', 413)
        if shutil.disk_usage(self.root).free < max(0, size - already_staged) + self.pending_disk_bytes():
            fail('本地空间不足以保存完整来源', 'ENERGY_DISK_FULL', 413)

    def reserve(self, ident, revision):
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.editable(doc)
            self.capacity(doc, 0)
            remaining = min(MAX_FILE, MAX_BATCH - sum(s['source'].get('size_bytes', 0) for s in doc['samples']),
                            MAX_STORE - self.committed_bytes() - sum(self.uploads.values()),
                            shutil.disk_usage(self.root).free - self.pending_disk_bytes())
            if remaining <= 0:
                fail('能量缓存或本地磁盘没有可预留的完整来源空间', 'ENERGY_QUOTA', 413)
            path = self.root / (uuid.uuid4().hex + '.upload')
            self.uploads[str(path)] = remaining
            return path, remaining

    def release(self, path):
        path.unlink(missing_ok=True)
        with self.guard:
            self.uploads.pop(str(path), None)

    def _sample(self, name, source, parsed, energy_basis=None):
        sample = {'id': 'es_' + uuid.uuid4().hex, 'name': name_valid(name), 'revision': 1,
                  'source': source, 'parsed': parsed, 'override': None, 'energy_basis': energy_basis,
                  'role': None, 'included': False, 'confirmed': False, 'accepted_warnings': False,
                  'confirmation_fingerprint': None, 'warning_acceptance_fingerprint': None}
        sample.update(role_origin=None, included_origin=None, assignment_reasons=[])
        sample['role_suggestion'] = suggestion(sample)
        return sample

    @staticmethod
    def file_fingerprint(path):
        size, sha = 0, hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(chunk)
                sha.update(chunk)
        return size, sha.hexdigest()

    def import_path(self, ident, revision, path, name, relative_path=None, task_source=None):
        from .parser import parse_energy_outcar
        size, sha = self.file_fingerprint(path)
        if not size or size > MAX_FILE:
            fail('OUTCAR为空或超过64 MiB上限', 'ENERGY_TOO_LARGE' if size else 'ENERGY_EMPTY_SOURCE', 413 if size else 400)
        relative_path = display_path(relative_path)
        # Parsing and publication are bounded to one collection mutation at a time.
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.editable(doc)
            reservation = self.uploads.pop(str(path), None)
            try:
                self.capacity(doc, size, already_staged=size)
                try:
                    text = path.read_text(encoding='utf-8-sig', errors='strict')
                except UnicodeDecodeError:
                    fail('OUTCAR必须为UTF-8文本，不能导入二进制或不完整编码', 'ENERGY_ENCODING_INVALID')
                if '\x00' in text:
                    fail('OUTCAR包含二进制字节', 'ENERGY_ENCODING_INVALID')
                parsed = parse_energy_outcar(text)
                source = {'kind': 'task_result' if task_source else 'local_upload', 'original_name': name_valid(name),
                          'relative_path': relative_path, 'sha256': sha, 'size_bytes': size, 'imported_at': now()}
                if task_source:
                    source.update(copy.deepcopy(task_source))
                sample = self._sample(name, source, parsed)
                target = self.directory(ident) / (sample['id'] + '.bin')
                # Never retain a parser input whose bytes changed during parsing.
                if self.file_fingerprint(path) != (size, sha):
                    fail('导入期间来源文件改变', 'ENERGY_SOURCE_CHANGED', 409)
                os.replace(path, target)
                doc['samples'].append(sample)
                doc['result'] = None
                doc['assignment_report'] = None
                try:
                    return self.save(doc)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
            finally:
                if reservation is not None:
                    self.uploads.pop(str(path), None)

    def manual_sample(self, body, kind='manual', relative_path=None):
        values = body['energy_fields']
        composition(body['composition'])
        if not finite(values.get(body['energy_basis'])):
            fail('手填能量必须声明一个有有限数值的能量口径', 'ENERGY_FIELD_REQUIRED')
        fields = {key: values.get(key) for key in FIELDS}
        if any(value is not None and not finite(value) for value in fields.values()):
            fail('手填能量不能包含NaN或Inf', 'ENERGY_NONFINITE')
        payload = {'composition': body['composition'], 'energy_fields': fields, 'energy_basis': body['energy_basis'],
                   'unit': body['unit'], 'reference_note': body.get('reference_note', '')}
        source = {'kind': kind, 'original_name': name_valid(body['name']), 'relative_path': display_path(relative_path),
                  'sha256': fingerprint(payload), 'size_bytes': 0, 'imported_at': now(),
                  'reference_note': body.get('reference_note', '')}
        parsed = {'energy_fields': fields, 'composition': body['composition'],
                  'status': {'completion': 'unknown', 'electronic_converged': None, 'ionic_converged': None,
                             'ionic_applicability': 'unknown'},
                  'metadata': {'method': 'unknown', 'support': {'automatic_comparison': True, 'reasons': []}},
                  'provenance': {'kind': kind, 'unit': body['unit']}, 'errors': [],
                  'warnings': ['人工值：运行及收敛状态未经自动核查'], 'issues': []}
        return self._sample(body['name'], source, parsed, body['energy_basis'])

    def manual(self, ident, revision, body):
        self.check_editable(ident, revision)
        sample = self.manual_sample(body)
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.editable(doc)
            self.capacity(doc, 0)
            doc['samples'].append(sample)
            doc['result'] = None
            doc['assignment_report'] = None
            return self.save(doc)

    def _csv_preview(self, doc, data, energy_basis=None):
        from .csv_input import parse
        preview = parse(data, doc['revision'], energy_basis, MAX_SAMPLES)
        try:
            self.capacity(doc, 0, preview['row_count'])
        except ToolboxError as exc:
            preview['issues'].append({'code': exc.code, 'message': str(exc)})
            preview['can_import'] = False
        return preview

    def csv_preview(self, ident, revision, data, energy_basis=None):
        with self.guard:
            doc = self.check_editable(ident, revision)
            return self._csv_preview(doc, data, energy_basis)

    def csv_import(self, ident, revision, data, energy_basis=None):
        with self.guard:
            doc = self.check_editable(ident, revision)
            preview = self._csv_preview(doc, data, energy_basis)
            if not preview['can_import']:
                details = ['第' + str(row['row_number']) + '行：' + '；'.join(i['message'] for i in row['issues'])
                           for row in preview['rows'] if row['issues']]
                details.extend(issue['message'] for issue in preview['issues'])
                codes = {issue['code'] for row in preview['rows'] for issue in row['issues']}
                code = 'ENERGY_BASIS_CONFLICT' if 'ENERGY_BASIS_CONFLICT' in codes else 'ENERGY_CSV_INVALID'
                if any(issue['code'] == 'ENERGY_QUOTA' for issue in preview['issues']):
                    code = 'ENERGY_QUOTA'
                fail('CSV未导入：' + '；'.join(details), code, 413 if code == 'ENERGY_QUOTA' else 400)
            samples = []
            for row in preview['rows']:
                sample = self.manual_sample({'name': row['name'], 'composition': row['composition'],
                        'energy_fields': {row['energy_basis']: row['energy_ev']}, 'energy_basis': row['energy_basis'],
                        'unit': row['unit'], 'reference_note': row['reference_note']}, 'csv', row['relative_path'])
                sample['source']['csv_metadata'] = row['csv_metadata']
                sample['source']['sha256'] = fingerprint({'payload_sha256': sample['source']['sha256'],
                                                         'csv_metadata': row['csv_metadata']})
                samples.append(sample)
            doc['samples'].extend(samples)
            doc['result'] = None
            doc['assignment_report'] = None
            return self.save(doc)

    @staticmethod
    def csv_template():
        from .csv_input import HEADERS
        out = io.StringIO(newline='')
        csv.writer(out).writerow(HEADERS)
        return out.getvalue()

    @staticmethod
    def csv_text(value):
        value = str(value)
        return "'" + value if value.startswith("'") or value.lstrip(' \r\n').startswith(('=', '+', '-', '@', '\t')) else value

    def samples_csv(self, ident, energy_basis, value_source='effective'):
        from .csv_input import HEADERS
        if energy_basis not in FIELDS or value_source not in {'original', 'effective'}:
            fail('样本表必须明确能量口径和原始/有效值选择', 'ENERGY_BASIS_CONFLICT')
        with self.guard:
            doc = self.read(ident)
            out = io.StringIO(newline='')
            writer = csv.writer(out)
            writer.writerow(HEADERS + ['value_source', 'original_energy_ev', 'override_note', 'source_note',
                                       'csv_metadata', 'csv_text_encoding'])
            for line, sample in enumerate(doc['samples'], 2):
                values = effective(sample) if value_source == 'effective' else sample['parsed']
                value = values['energy_fields'].get(energy_basis)
                if not finite(value):
                    fail(f'第{line}行（{sample["name"]}）缺少所选{energy_basis}的{value_source}有限能量字段，不会回退其他字段',
                         'ENERGY_FIELD_REQUIRED')
                try:
                    comp = composition(values.get('composition'))
                except ToolboxError:
                    fail(f'第{line}行（{sample["name"]}）缺少所选{value_source}组成', 'ENERGY_COMPOSITION_REQUIRED')
                original = sample['parsed']['energy_fields'].get(energy_basis)
                source_note = ('选择' + ('人工覆盖后的有效值' if value_source == 'effective' else '原始解析值') +
                               '；CSV回导不继承原始来源验证、运行收敛、核对或锁定')
                metadata = sample['source'].get('csv_metadata') or {}
                override_note = ((sample.get('override') or {}).get('note') or
                                 next(reversed(metadata.get('override_notes') or []), ''))
                writer.writerow([self.csv_text(sample['name']), ' '.join(f'{element}:{count}' for element, count in comp.items()),
                    energy_basis, value, 'eV', self.csv_text(sample['source'].get('relative_path') or ''),
                    self.csv_text(sample['source'].get('reference_note') or ''), value_source, original if finite(original) else '',
                    self.csv_text(override_note), self.csv_text(source_note),
                    json.dumps(metadata, ensure_ascii=False, allow_nan=False), 'apostrophe-prefix'])
            return out.getvalue()

    def configure(self, ident, body, automatic=False):
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, body['expected_revision'])
            self.legacy_api(doc)
            self.editable(doc)
            old = copy.deepcopy(doc)
            if body.get('title') is not None:
                doc['title'] = body['title'].strip() or '能量比较'
            groups = body['groups']
            previous_groups = {group['id']: group for group in old['groups']}
            for group in groups:
                previous = previous_groups.get(group['id']) or {}
                origins = dict(previous.get('reference_origins') or {})
                if automatic:
                    origins.update(group.get('reference_origins') or {})
                else:
                    origins.update({key: 'manual' for key, value in (group.get('reference_origins') or {}).items() if value == 'manual'})
                    for key in ('clean_sample_id', 'adsorbate_sample_id'):
                        # The UI's empty select value is the same missing reference as None.
                        # An explicit manual origin still protects a deliberately empty choice.
                        group[key] = group.get(key) or None
                        if group[key] != (previous.get(key) or None):
                            origins[key] = 'manual'
                    before_refs, after_refs = previous.get('element_references') or {}, group.get('element_references') or {}
                    for element in set(before_refs) | set(after_refs):
                        if before_refs.get(element) != after_refs.get(element):
                            origins['element:' + element] = 'manual'
                    if previous and group.get('reference_units') != previous.get('reference_units'):
                        origins['reference_units'] = 'manual'
                    previous_targets = {t['sample_id']: t for t in previous.get('targets', [])}
                    for target in group['targets']:
                        former = previous_targets.get(target['sample_id'])
                        if former and target.get('adsorbate_count') != former.get('adsorbate_count'):
                            origins['target:' + target['sample_id']] = 'manual'
                group['reference_origins'] = origins
            if not doc['legacy_mode'] and any(g['kind'] != doc['analysis_kind'] for g in groups):
                fail('同一分析只能包含所选分析类型', 'ENERGY_ANALYSIS_KIND_CONFLICT')
            ids = [g['id'] for g in groups]
            if len(ids) != len(set(ids)):
                fail('比较组ID不能重复', 'ENERGY_GROUP_INVALID')
            for group in groups:
                target_ids = [t['sample_id'] for t in group['targets']]
                if len(target_ids) != len(set(target_ids)):
                    fail('同组目标样本不能重复', 'ENERGY_GROUP_INVALID')
            doc['groups'] = groups
            by_id = {s['id']: s for s in doc['samples']}
            updates = {s['sample_id']: s for s in body['samples']}
            if len(updates) != len(body['samples']) or set(updates) - set(by_id):
                fail('确认表样本不存在或重复', 'ENERGY_SAMPLE_INVALID')
            for ident_sample, row in updates.items():
                sample = by_id[ident_sample]
                if not automatic:
                    for field, origin_field in (('role', 'role_origin'), ('included', 'included_origin')):
                        if row.get(origin_field) == 'manual' or row[field] != sample[field]:
                            sample[origin_field] = 'manual'
                else:
                    sample.update({key: row.get(key) for key in ('role_origin', 'included_origin')})
                    sample['assignment_reasons'] = row.get('assignment_reasons', [])
                if row.get('name') is not None:
                    sample['name'] = name_valid(row['name'])
                if row.get('override'):
                    override = row['override']
                    if override.get('composition'):
                        composition(override['composition'])
                    if override.get('energy_fields'):
                        if not override.get('energy_basis') or not finite(override['energy_fields'].get(override['energy_basis'])):
                            fail('人工覆盖必须声明一个有值能量口径', 'ENERGY_FIELD_REQUIRED')
                sample.update({key: row[key] for key in ('role', 'included', 'override', 'confirmed', 'accepted_warnings')})
                if not doc['legacy_mode'] and sample['role'] is not None:
                    roles = {'clean_slab', 'adsorbate', 'adsorbed'} if doc['analysis_kind'] == 'adsorption' else {'material', 'element_reference'}
                    if sample['role'] not in roles:
                        fail('样本角色与所选分析类型不符', 'ENERGY_ANALYSIS_KIND_CONFLICT')
                sample['revision'] += 1
            old_by_id = {s['id']: s for s in old['samples']}
            for sample in doc['samples']:
                previous = old_by_id[sample['id']]
                before = confirmation(previous, old['groups'], old['samples'])
                after = confirmation(sample, groups, doc['samples'])
                changed = before != after or (previous['confirmed'] and previous.get('confirmation_fingerprint') != before)
                explicit = sample['id'] in updates
                if changed and (not explicit or (previous['confirmed'] and not doc['legacy_mode'])):
                    sample['confirmed'] = False
                sample['confirmation_fingerprint'] = ((after if explicit else previous.get('confirmation_fingerprint'))
                    if sample['confirmed'] else None)
                before_risk, after_risk = warning_acceptance(previous), warning_acceptance(sample)
                risk_changed = before_risk != after_risk or (previous['accepted_warnings'] and
                    previous.get('warning_acceptance_fingerprint') != before_risk)
                if risk_changed and (not explicit or not doc['legacy_mode']):
                    sample['accepted_warnings'] = False
                sample['warning_acceptance_fingerprint'] = ((after_risk if explicit else previous.get('warning_acceptance_fingerprint'))
                    if sample['accepted_warnings'] else None)
            old_groups = {group['id']: group for group in old['groups']}
            for group in groups:
                previous = old_groups.get(group['id'])
                if previous and previous['basis_confirmed'] and not doc['legacy_mode'] and (
                        group_confirmation(previous, old['samples']) != group_confirmation(group, doc['samples'])):
                    group['basis_confirmed'] = False
            if doc['legacy_mode']:
                kinds = {group['kind'] for group in groups}
                doc['analysis_kind'] = next(iter(kinds)) if len(kinds) == 1 else None
            doc['result'] = None
            doc['assignment_report'] = body.get('assignment_report') if automatic else None
            return self.save(doc)

    def autofill(self, ident, revision):
        from .assignment import plan
        with self.guard:
            doc = self.check_editable(ident, revision)
            self.legacy_api(doc)
            if doc['legacy_mode']:
                fail('旧分析请先按明确比较关系复制为所选类型，再自动分配', 'ENERGY_LEGACY_ASSIGNMENT', 409)
            if len(doc['groups']) > 1:
                fail('自动分配需单一参考条件，请分别建立分析', 'ENERGY_GROUP_INVALID')
            return self.configure(ident, plan(doc), automatic=True)

    @staticmethod
    def removal(doc, sample_ids, clear_all):
        available = {sample['id'] for sample in doc['samples']}
        if clear_all and sample_ids:
            fail('清空与指定样本删除不能同时使用', 'ENERGY_SAMPLE_INVALID')
        if len(sample_ids) != len(set(sample_ids)) or set(sample_ids) - available:
            fail('删除样本不存在或重复', 'ENERGY_SAMPLE_INVALID')
        selected = available if clear_all else set(sample_ids)
        if not selected and not clear_all:
            fail('请明确选择要删除的样本', 'ENERGY_SAMPLE_INVALID')
        affected, cleared = set(), []
        by_id = {sample['id']: sample for sample in doc['samples']}
        for group in doc['groups']:
            refs = {group.get('clean_sample_id'), group.get('adsorbate_sample_id')} | set((group.get('element_references') or {}).values())
            for target in group['targets']:
                target_refs = refs
                if group['kind'] == 'formation' and target['sample_id'] in by_id:
                    elements = set(effective(by_id[target['sample_id']])['composition'] or {})
                    target_refs = {(group.get('element_references') or {}).get(element) for element in elements}
                if target['sample_id'] in selected or target_refs & selected:
                    affected.add(target['sample_id'])
            for key in ('clean_sample_id', 'adsorbate_sample_id'):
                if group.get(key) in selected:
                    cleared.append(group['id'] + ':' + key)
            cleared.extend(group['id'] + ':element:' + element for element, sid in
                           (group.get('element_references') or {}).items() if sid in selected)
        return {'sample_ids': sorted(selected), 'removed_count': len(selected),
                'affected_target_ids': sorted(affected), 'cleared_reference_keys': cleared}

    def removal_preview(self, ident, revision, sample_ids, clear_all=False):
        with self.guard:
            doc = self.check_editable(ident, revision)
            return self.removal(doc, sample_ids, clear_all)

    def remove_samples(self, ident, revision, sample_ids, clear_all=False):
        with self.guard:
            doc = self.check_editable(ident, revision)
            preview = self.removal(doc, sample_ids, clear_all)
            old = copy.deepcopy(doc)
            selected = set(preview['sample_ids'])
            doc['samples'] = [sample for sample in doc['samples'] if sample['id'] not in selected]
            for group in doc['groups']:
                origins = group.setdefault('reference_origins', {})
                for key in ('clean_sample_id', 'adsorbate_sample_id'):
                    if group.get(key) in selected:
                        group[key] = None
                        origins[key] = 'manual'
                for element, sid in list((group.get('element_references') or {}).items()):
                    if sid in selected:
                        del group['element_references'][element]
                        origins['element:' + element] = 'manual'
                group['targets'] = [target for target in group['targets'] if target['sample_id'] not in selected]
                for sid in selected:
                    origins.pop('target:' + sid, None)
                if clear_all:
                    # A full reset has no surviving dependents; the next import is a new batch.
                    group['reference_origins'] = {}
                    group['reference_units'] = 1
                    group['basis_confirmed'] = False
            old_samples = {sample['id']: sample for sample in old['samples']}
            for sample in doc['samples']:
                if confirmation(old_samples[sample['id']], old['groups'], old['samples']) != confirmation(sample, doc['groups'], doc['samples']):
                    sample.update(confirmed=False, confirmation_fingerprint=None)
                    sample['revision'] += 1
            old_groups = {group['id']: group for group in old['groups']}
            for group in doc['groups']:
                if group_confirmation(old_groups[group['id']], old['samples']) != group_confirmation(group, doc['samples']):
                    group['basis_confirmed'] = False
            doc['result'] = None
            doc['assignment_report'] = None
            # Only membership is removed. Owned immutable .bin files remain until collection
            # deletion and continue to count toward MAX_STORE; shared/task/original data is untouched.
            return self.save(doc), preview

    def lock(self, ident, revision):
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.legacy_api(doc)
            self.verify_sources(doc)
            for sample in doc['samples']:
                if sample['included'] and not groups_for(sample['id'], doc['groups']):
                    fail(sample['name'] + ' 已纳入但未绑定计算关系，请配置参考/目标或取消纳入',
                         'ENERGY_UNBOUND_SAMPLE', 409)
            # Reuse the existing deterministic validators and formulas; no source reparse.
            calculate(doc)
            doc['locked'] = True
            doc['lock_fingerprint'] = input_fingerprint(doc)
            return self.save(doc)

    def unlock(self, ident, revision):
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.legacy_api(doc)
            doc['locked'] = False
            doc['lock_fingerprint'] = None
            return self.save(doc)

    def copy_analysis(self, ident, revision, analysis_kind, title=None, group_id=None, workflow=None):
        with self.guard:
            source = self.read(ident)
            self.expect(source, revision)
            selected = [group for group in source['groups'] if group['kind'] == analysis_kind and
                        (group_id is None or group['id'] == group_id)]
            empty_typed_copy = workflow == 'cards' and not source['groups'] and group_id is None
            if not selected and not empty_typed_copy:
                fail('所选类型或比较关系不存在', 'ENERGY_GROUP_INVALID')
            if len(selected) > 1 and workflow != 'cards':
                fail('旧分析含多个参考条件，请明确选择一个比较关系复制', 'ENERGY_COPY_GROUP_REQUIRED', 409)
            used = {sample['id'] for sample in source['samples'] if empty_typed_copy or groups_for(sample['id'], selected)}
            projected = {**source, 'samples': [s for s in source['samples'] if s['id'] in used]}
            self.verify_sources(projected)
            doc = self.create(title or source['title'], analysis_kind, workflow)
            directory = self.directory(doc['id'])
            try:
                mapping = {}
                for original in projected['samples']:
                    sample = copy.deepcopy(original)
                    sample['id'] = 'es_' + uuid.uuid4().hex
                    mapping[original['id']] = sample['id']
                    sample.update(revision=1, confirmed=False, accepted_warnings=False,
                                  confirmation_fingerprint=None, warning_acceptance_fingerprint=None)
                    if sample['source']['kind'] in {'local_upload', 'task_result'}:
                        dest = directory / (sample['id'] + '.bin')
                        self.capacity(doc, sample['source']['size_bytes'])
                        shutil.copyfile(self.directory(ident) / (original['id'] + '.bin'), dest)
                        if self.file_fingerprint(dest) != (sample['source']['size_bytes'], sample['source']['sha256']):
                            fail('缓存复制校验失败', 'ENERGY_SOURCE_CHANGED', 409)
                    doc['samples'].append(sample)
                doc['groups'] = copy.deepcopy(selected)
                for group in doc['groups']:
                    group['basis_confirmed'] = False
                    group['reference_origins'] = {
                        ('target:' + mapping.get(key.split(':', 1)[1], key.split(':', 1)[1])
                         if key.startswith('target:') else key): origin
                        for key, origin in (group.get('reference_origins') or {}).items()}
                    for key in ('clean_sample_id', 'adsorbate_sample_id'):
                        if group.get(key) is not None:
                            group[key] = mapping.get(group[key], group[key])
                    group['element_references'] = {element: mapping.get(sid, sid) for element, sid in
                                                    (group.get('element_references') or {}).items()}
                    for target in group['targets']:
                        target['sample_id'] = mapping.get(target['sample_id'], target['sample_id'])
                if workflow == 'cards':
                    from .cards import fresh, CONFIG
                    doc['groups'] = [fresh({key: group.get(key) for key in CONFIG}) for group in doc['groups']]
                return self.save(doc)
            except BaseException:
                shutil.rmtree(directory)
                raise

    def verify_sources(self, doc):
        for sample in doc['samples']:
            if sample['source']['kind'] in {'local_upload', 'task_result'}:
                path = self.directory(doc['id']) / (sample['id'] + '.bin')
                if path.is_symlink() or not path.is_file() or self.file_fingerprint(path) != (
                        sample['source']['size_bytes'], sample['source']['sha256']):
                    from .errors import EnergyError
                    raise EnergyError('ENERGY_SOURCE_CHANGED', '原始来源缓存缺失或指纹改变，需重新导入', 409,
                                      sample_id=sample['id'], field='samples.' + sample['id'] + '.source')

    def calculate(self, ident, revision):
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.legacy_api(doc)
            self.verify_sources(doc)
            self.valid_lock(doc)
            doc['result'] = {**calculate(doc), 'calculated_at': now()}
            return self.save(doc)

    def reuse(self, ident, revision, source_ident, sample_id):
        with self.guard:
            doc = self.check_editable(ident, revision)
            source = self.read(source_ident)
            self.verify_sources(source)
            selected = next((s for s in source['samples'] if s['id'] == sample_id), None)
            if not selected or selected['source']['kind'] != 'task_result':
                fail('只能复用已有完整任务缓存样本', 'ENERGY_CACHE_INCOMPLETE', 409)
            self.capacity(doc, selected['source']['size_bytes'])
            sample = copy.deepcopy(selected)
            sample.update(id='es_' + uuid.uuid4().hex, revision=1, role=None, included=False, confirmed=False,
                          accepted_warnings=False, confirmation_fingerprint=None,
                          warning_acceptance_fingerprint=None, override=None,
                          role_origin=None, included_origin=None, assignment_reasons=[])
            sample['source']['reused_from'] = {'collection_id': source_ident, 'sample_id': sample_id}
            src = self.directory(source_ident) / (sample_id + '.bin')
            dest = self.directory(ident) / (sample['id'] + '.bin')
            shutil.copyfile(src, dest)
            if self.file_fingerprint(dest) != (sample['source']['size_bytes'], sample['source']['sha256']):
                dest.unlink(missing_ok=True)
                fail('缓存复制校验失败', 'ENERGY_SOURCE_CHANGED', 409)
            doc['samples'].append(sample)
            doc['result'] = None
            doc['assignment_report'] = None
            try:
                return self.save(doc)
            except BaseException:
                dest.unlink(missing_ok=True)
                raise

    def export(self, ident):
        with self.guard:
            doc = self.read(ident)
            self.legacy_api(doc)
            self.verify_sources(doc)
            self.valid_lock(doc)
            if not doc['result'] or doc['result']['input_fingerprint'] != input_fingerprint(doc):
                fail('当前定义尚无有效计算结果，请重新计算', 'ENERGY_RESULT_STALE', 409)
            def scrub(value):
                if isinstance(value, dict):
                    return {k: scrub(v) for k, v in value.items() if k not in
                            {'remote_directory', 'remote_path', 'identity_file', 'known_hosts_path'}}
                if isinstance(value, list):
                    return [scrub(v) for v in value]
                return value
            return scrub(doc)

    def csv(self, ident):
        doc = self.export(ident)
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['record_type', 'group_id', 'sample_id', 'name', 'energy_basis', 'delta_ev',
                         'normalized_ev', 'unit', 'source_json', 'parsed_json', 'override_json', 'details_json', 'warnings_json'])
        def text(value):
            value = str(value)
            return "'" + value if value.lstrip(' \r\n').startswith(('=', '+', '-', '@', '\t')) else value
        dump = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False)
        for sample in doc['samples']:
            writer.writerow(['sample', '', sample['id'], text(sample['name']), sample.get('energy_basis') or '', '', '', 'eV',
                             dump(sample['source']), dump(sample['parsed']), dump(sample['override']),
                             dump({key: sample[key] for key in ('role', 'included', 'confirmed', 'accepted_warnings')}),
                             dump(sample['parsed']['warnings'])])
        for group in doc['result']['groups']:
            for row in group['rows']:
                writer.writerow(['result', text(group['id']), row['sample_id'], text(row['name']), group['energy_basis'],
                                 row['delta_ev'], row['normalized_ev'], row['unit'], '', '', '',
                                 dump({'group': group, 'row': row}), dump(row['warnings'])])
        return out.getvalue()

    def delete(self, ident):
        with self.guard:
            path = self.directory(ident)
            shutil.rmtree(path)

    def close(self):
        if self.task_sources:
            self.task_sources.close()
