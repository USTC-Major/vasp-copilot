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
from .calculation import (FIELDS, calculate, composition, confirmation, effective, finite,
                          fingerprint, input_fingerprint)

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
                return json.loads((self.directory(ident) / 'metadata.json').read_text(encoding='utf-8'))
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
        doc['revision'] += 1
        doc['updated_at'] = now()
        self.write_json(self.directory(doc['id']) / 'metadata.json', doc)
        return doc

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

    def create(self, title):
        with self.guard:
            if len(self.list()) >= 100:
                fail('最多保存100个能量比较集', 'ENERGY_QUOTA', 413)
            ident = 'ec_' + uuid.uuid4().hex
            (self.root / ident).mkdir()
            doc = {'id': ident, 'schema_version': 'pp.energy.v1', 'title': title.strip() or '能量比较',
                   'revision': 0, 'created_at': now(), 'updated_at': now(), 'samples': [], 'groups': [],
                   'result': None, 'limits': LIMITS}
            try:
                return self.save(doc)
            except BaseException:
                shutil.rmtree(self.root / ident)
                raise

    @staticmethod
    def expect(doc, revision):
        if revision != doc['revision']:
            fail('比较集已被修改，请重读后保存', 'ENERGY_REVISION_CONFLICT', 409)

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
                  'confirmation_fingerprint': None}
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
        sample = self.manual_sample(body)
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.capacity(doc, 0)
            doc['samples'].append(sample)
            doc['result'] = None
            return self.save(doc)

    def csv_import(self, ident, revision, data):
        if len(data) > 1024 * 1024:
            fail('手填CSV最多1 MiB', 'ENERGY_TOO_LARGE', 413)
        from .schemas import Manual
        from pydantic import ValidationError
        try:
            reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
            required = {'name', 'composition', 'energy_basis', 'energy_ev', 'unit'}
            if not required <= set(reader.fieldnames or []):
                fail('CSV缺少必需列：' + ', '.join(sorted(required)), 'ENERGY_CSV_INVALID')
            samples = []
            for row in reader:
                if len(samples) >= MAX_SAMPLES:
                    fail('CSV最多100行', 'ENERGY_QUOTA', 413)
                value = float(row['energy_ev'])
                basis = row['energy_basis']
                body = Manual.model_validate({'expected_revision': revision, 'name': row['name'],
                        'composition': json.loads(row['composition']), 'energy_fields': {basis: value},
                        'energy_basis': basis, 'unit': row['unit'], 'reference_note': row.get('reference_note') or ''})
                samples.append(self.manual_sample(body.model_dump(exclude={'expected_revision'}), 'csv', row.get('relative_path')))
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, csv.Error, ValidationError) as exc:
            fail('CSV值、组成JSON、有限能量或单位无效：' + type(exc).__name__, 'ENERGY_CSV_INVALID')
        if not samples:
            fail('CSV没有样本', 'ENERGY_CSV_INVALID')
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.capacity(doc, 0, len(samples))
            doc['samples'].extend(samples)
            doc['result'] = None
            return self.save(doc)

    def configure(self, ident, body):
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, body['expected_revision'])
            old = copy.deepcopy(doc)
            if body.get('title') is not None:
                doc['title'] = body['title'].strip() or '能量比较'
            groups = body['groups']
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
                sample['revision'] += 1
            old_by_id = {s['id']: s for s in old['samples']}
            for sample in doc['samples']:
                changed = confirmation(old_by_id[sample['id']], old['groups']) != confirmation(sample, groups)
                if changed and sample['id'] not in updates:
                    sample.update(confirmed=False, accepted_warnings=False, confirmation_fingerprint=None)
                elif sample['id'] in updates:
                    sample['confirmation_fingerprint'] = confirmation(sample, groups) if sample['confirmed'] else None
                    if not sample['confirmed']:
                        sample['accepted_warnings'] = False
            doc['result'] = None
            return self.save(doc)

    def verify_sources(self, doc):
        for sample in doc['samples']:
            if sample['source']['kind'] in {'local_upload', 'task_result'}:
                path = self.directory(doc['id']) / (sample['id'] + '.bin')
                if path.is_symlink() or not path.is_file() or self.file_fingerprint(path) != (
                        sample['source']['size_bytes'], sample['source']['sha256']):
                    fail('原始来源缓存缺失或指纹改变，需重新导入', 'ENERGY_SOURCE_CHANGED', 409)

    def calculate(self, ident, revision):
        with self.guard:
            doc = self.read(ident)
            self.expect(doc, revision)
            self.verify_sources(doc)
            doc['result'] = {**calculate(doc), 'calculated_at': now()}
            return self.save(doc)

    def reuse(self, ident, revision, source_ident, sample_id):
        with self.guard:
            source = self.read(source_ident)
            self.verify_sources(source)
            selected = next((s for s in source['samples'] if s['id'] == sample_id), None)
            if not selected or selected['source']['kind'] != 'task_result':
                fail('只能复用已有完整任务缓存样本', 'ENERGY_CACHE_INCOMPLETE', 409)
            doc = self.read(ident)
            self.expect(doc, revision)
            self.capacity(doc, selected['source']['size_bytes'])
            sample = copy.deepcopy(selected)
            sample.update(id='es_' + uuid.uuid4().hex, revision=1, role=None, included=False, confirmed=False,
                          accepted_warnings=False, confirmation_fingerprint=None, override=None)
            sample['source']['reused_from'] = {'collection_id': source_ident, 'sample_id': sample_id}
            src = self.directory(source_ident) / (sample_id + '.bin')
            dest = self.directory(ident) / (sample['id'] + '.bin')
            shutil.copyfile(src, dest)
            if self.file_fingerprint(dest) != (sample['source']['size_bytes'], sample['source']['sha256']):
                dest.unlink(missing_ok=True)
                fail('缓存复制校验失败', 'ENERGY_SOURCE_CHANGED', 409)
            doc['samples'].append(sample)
            doc['result'] = None
            try:
                return self.save(doc)
            except BaseException:
                dest.unlink(missing_ok=True)
                raise

    def export(self, ident):
        with self.guard:
            doc = self.read(ident)
            self.verify_sources(doc)
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
