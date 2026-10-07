"""Confirmed local assembly. Source bodies live only in memory and private artifacts.

Previews are volatile metadata; immutable artifacts publish by directory rename.
No FileStore, workflow, AI, shell, or scientific recommendation dependency.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.input_validation import InputValidationError, _ELEMENTS, validate_poscar, validate_potcar
from ..contracts import ToolboxError
from ..storage import atomic_json, read_object
from .filesystem import checked_path, fail, read_source
from .metadata import dataset, decode, digest, metadata
from .service import exact, revision

_ID = re.compile(r'[0-9a-f]{32}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_META = ('element', 'variant', 'family', 'lexch', 'zval', 'enmax_ev', 'dataset_date', 'title')
_ROW = ('position', 'element', 'atom_count', 'dataset_id', 'variant', 'title', 'decoded_sha256', 'source_sha256')
_ARTIFACT = {'artifact_id', 'preview_id', 'selection_digest', 'structure_sha256', 'library_id', 'index_revision',
             'status', 'size_bytes', 'sha256', 'created_at', 'expires_at', 'rows'}


def timestamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace('+00:00', 'Z')


def validation(fn, *args):
    try:
        return fn(*args)
    except InputValidationError as exc:
        raise ToolboxError(exc.code, exc.message, 400, False) from None


def identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        fail('INVALID_REQUEST', '标识格式无效')
    return value


def public_candidate(row):
    # Index data is untrusted local persistence too. Only grammar-limited titles
    # and bounded metadata paths may enter a preview; never arbitrary body fields.
    value = copy.deepcopy(row)
    path = value.get('relative_path')
    if (not isinstance(path, str) or len(path) > 4096 or not path or
            any(ord(c) < 32 or ord(c) == 127 for c in path) or Path(path).is_absolute() or '..' in Path(path).parts):
        raise ValueError('Invalid metadata path')
    if value.get('title') is not None and not re.fullmatch(
            r'(?:PAW_PBE|PAW|PAW_GGA|PAW_RPBE|US|NC) [A-Z][a-z]?(?:_[A-Za-z0-9]+)*(?: (?:\d{1,2}[A-Za-z]{3}\d{2,4}|\d{4}-\d{2}-\d{2}))?', value['title']):
        raise ValueError('Invalid metadata title')
    if value.get('element') not in _ELEMENTS:
        raise ValueError('Invalid metadata element')
    if value.get('family') != 'PAW_PBE' or value.get('compression') not in {'raw', 'gzip', 'Z'}:
        raise ValueError('Invalid metadata family or compression')
    if not isinstance(value.get('variant'), str) or not re.fullmatch(r'[A-Z][a-z]?(?:_[A-Za-z0-9]+)*', value['variant']):
        raise ValueError('Invalid metadata variant')
    if value.get('lexch') is not None and (not isinstance(value['lexch'], str) or not re.fullmatch(r'[A-Za-z]{1,8}', value['lexch'])):
        raise ValueError('Invalid metadata exchange marker')
    date = value.get('dataset_date')
    if date is not None and (not isinstance(date, str) or not re.fullmatch(r'\d{1,2}[A-Za-z]{3}\d{2,4}|\d{4}-\d{2}-\d{2}', date)):
        raise ValueError('Invalid metadata date')
    for field in ('zval', 'enmax_ev'):
        if value.get(field) is not None and (type(value[field]) not in (int, float) or not math.isfinite(value[field])):
            raise ValueError('Invalid numeric metadata')
    if value.get('duplicate_of') is not None:
        identifier(value['duplicate_of'])
    # Ready/ambiguous rows can only carry these two service-generated notices.
    # Reconstruct their text so altered local index messages cannot leak a body.
    notices = {'POTCAR_DUPLICATE_CONTENT': '与另一候选解码后内容相同；保留独立路径供查看',
               'POTCAR_VARIANT_AMBIGUOUS': '相同变体存在不同内容；必须明确区分数据集'}
    if any(entry['code'] not in notices for entry in value['issues']):
        raise ValueError('Invalid selectable metadata issues')
    value['issues'] = [{'code': entry['code'], 'message': notices[entry['code']]} for entry in value['issues']]
    identifier(value.get('dataset_id'))
    for field in ('decoded_sha256', 'source_sha256'):
        if not isinstance(value.get(field), str) or not _HASH.fullmatch(value[field]):
            raise ValueError('Invalid metadata fingerprint')
    return value


class PotcarAssemblyService:
    def __init__(self, libraries):
        self.libraries = libraries
        self.limits = libraries.limits
        self.root = libraries.root / 'artifacts'
        self.root.mkdir(exist_ok=True)
        checked_path(str(self.root), directory=True)
        self.previews = {}

    def _snapshot(self, library_id, index_revision=None):
        svc = self.libraries
        if svc._closed:
            fail('SERVICE_CLOSED', '服务正在关闭；请重启后重试', 503, True)
        library = svc._library(identifier(library_id))
        svc._mutable(library)
        checked_path(library['root_path'], directory=True)
        index = svc._index(library)
        if index is None or (index_revision is not None and revision(index_revision) != index['index_revision']):
            fail('INDEX_REVISION_CONFLICT', '索引不存在或已更新；请重新扫描并刷新选择', 409, True)
        return library, index

    def preview(self, payload):
        exact(payload, {'library_id', 'index_revision', 'poscar_text', 'dataset_ids', 'legacy_species'},
              {'library_id', 'index_revision', 'poscar_text'})
        revision(payload['index_revision'])
        if not isinstance(payload['poscar_text'], str):
            fail('INVALID_REQUEST', '请提供 POSCAR 文本')
        info = validation(validate_poscar, payload['poscar_text'])
        species = info.elements
        if info.vasp4:
            species = payload.get('legacy_species')
            if species is None:
                fail('LEGACY_SPECIES_REQUIRED', '旧格式 POSCAR 缺少元素名；请按数量行明确映射每个物种块')
            if (not isinstance(species, list) or len(species) != len(info.counts) or
                    any(not isinstance(e, str) or e not in _ELEMENTS for e in species)):
                fail('INVALID_REQUEST', '旧格式物种映射必须与数量行逐项对应，且使用有效元素符号')
        elif 'legacy_species' in payload:
            fail('INVALID_REQUEST', '含元素名的 POSCAR 不接受旧格式映射')
        # The shared parser treats unknown coordinate modes as direct. This
        # local boundary refuses typos without changing its other consumers.
        lines = payload['poscar_text'].lstrip('\ufeff').splitlines()
        mode_line = 6 if info.vasp4 else 7
        mode_line += int(info.selective_dynamics)
        if lines[mode_line].strip()[0].lower() not in {'d', 'c', 'k'}:
            fail('POSCAR_MODE_UNSUPPORTED', 'POSCAR 坐标模式需明确为 Direct 或 Cartesian')
        if len(species) > self.limits.max_species_blocks:
            fail('ASSEMBLY_LIMIT', '结构物种块数量超过拼接上限')
        selected = payload.get('dataset_ids', [None] * len(species))
        if (not isinstance(selected, list) or len(selected) != len(species) or
                any(v is not None and (not isinstance(v, str) or not _ID.fullmatch(v)) for v in selected)):
            fail('INVALID_REQUEST', '数据集选择必须与物种块逐项对应')
        with self.libraries._lock:
            library, index = self._snapshot(payload['library_id'], payload['index_revision'])
            rows, blockers, bindings = [], [], []
            candidate_count = 0
            for pos, (element, count, chosen) in enumerate(zip(species, info.counts, selected), 1):
                candidates = [public_candidate(row) for row in index['datasets']
                              if row['element'] == element and row['status'] in {'ready', 'ambiguous'}]
                candidate_count += len(candidates)
                if candidate_count > self.limits.max_preview_candidates:
                    fail('ASSEMBLY_LIMIT', '候选列表超过预览上限；请使用更小的具体集合')
                if chosen is not None and chosen not in {row['dataset_id'] for row in candidates}:
                    fail('DATASET_SELECTION_INVALID', '选中数据集不属于本库的兼容候选；请刷新并重新选择', 409, True)
                if chosen is not None:
                    code, message = 'USER_SELECTED', '用户明确选择；请核对本次顺序与变体'
                elif len(candidates) == 1:
                    chosen = candidates[0]['dataset_id']
                    code, message = 'UNIQUE_COMPATIBLE', '仅有一个格式兼容候选；唯一可用不代表科学最优，请核对变体'
                elif candidates:
                    code, message = 'SELECTION_REQUIRED', '有多个兼容候选；请明确选择变体或来源项'
                else:
                    code, message = 'NO_COMPATIBLE_DATASET', '本库没有此元素的受支持单数据集候选；请更换库或补充来源后扫描'
                if chosen is None:
                    blockers.append({'code': 'POTCAR_' + code, 'message': message, 'position': pos})
                else:
                    bindings.append(next(copy.deepcopy(row) for row in candidates if row['dataset_id'] == chosen))
                rows.append({'position': pos, 'element': element, 'atom_count': count, 'dataset_id': chosen,
                             'candidates': candidates, 'reason': {'code': code, 'message': message}})
            structure_sha256 = digest(payload['poscar_text'].encode('utf-8'))
            pin = {'structure_sha256': structure_sha256, 'species': list(species), 'counts': list(info.counts),
                   'source_token': library['source_token'], 'library_id': library['library_id'],
                   'index_revision': index['index_revision'], 'datasets': bindings, 'selected': [r['dataset_id'] for r in rows]}
            selection_digest = digest(json.dumps(pin, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())
            epoch = time.time()
            self.previews = {key: value for key, value in self.previews.items() if value['expiry'] > epoch}
            if len(self.previews) >= self.limits.max_previews:
                fail('PREVIEW_BUSY', '有效预览数量达到上限；请稍后重试', 409, True)
            preview_id = uuid.uuid4().hex
            response = {'preview_id': preview_id, 'selection_digest': selection_digest, 'structure_sha256': structure_sha256,
                        'library': {key: library[key] for key in ('library_id', 'display_name', 'version_note')},
                        'rows': rows, 'blockers': blockers, 'expires_at': timestamp(epoch + self.limits.preview_ttl)}
            response['library']['index_revision'] = index['index_revision']
            self.previews[preview_id] = {'response': response, 'pin': pin, 'expiry': epoch + self.limits.preview_ttl}
            return copy.deepcopy(response)

    def _artifact_directory(self, artifact_id):
        identifier(artifact_id)
        return self.root / artifact_id

    def _load(self, artifact_id):
        folder = self._artifact_directory(artifact_id)
        if not folder.exists():
            fail('ARTIFACT_NOT_FOUND', '生成产物不存在；请重新选择并生成', 404, True)
        try:
            checked_path(str(folder), directory=True)
            path = checked_path(str(folder / 'manifest.json'))
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('Manifest limit')
            stored = read_object(path)
            if set(stored) != {'schema_version', 'artifact', 'request_digest', 'key_sha256'} or stored['schema_version'] != 1:
                raise ValueError('Manifest schema')
            value = stored['artifact']
            if not isinstance(value, dict) or set(value) != _ARTIFACT or value['artifact_id'] != artifact_id or value['status'] != 'ready':
                raise ValueError('Artifact schema')
            for field in ('artifact_id', 'preview_id', 'library_id'):
                identifier(value[field])
            for field in ('selection_digest', 'structure_sha256', 'sha256'):
                if not isinstance(value[field], str) or not _HASH.fullmatch(value[field]):
                    raise ValueError('Artifact fingerprint')
            for field in ('request_digest', 'key_sha256'):
                if not isinstance(stored[field], str) or not _HASH.fullmatch(stored[field]):
                    raise ValueError('Request fingerprint')
            if (type(value['size_bytes']) is not int or not 0 < value['size_bytes'] <= self.limits.max_assembly or
                    type(value['index_revision']) is not int or value['index_revision'] < 1 or
                    not isinstance(value['rows'], list) or not 0 < len(value['rows']) <= self.limits.max_species_blocks):
                raise ValueError('Artifact bounds')
            for pos, row in enumerate(value['rows'], 1):
                if not isinstance(row, dict) or set(row) != set(_ROW) or row['position'] != pos or row['element'] not in _ELEMENTS or type(row['atom_count']) is not int or row['atom_count'] < 0:
                    raise ValueError('Artifact rows')
                identifier(row['dataset_id'])
                if not isinstance(row['variant'], str) or not re.fullmatch(r'[A-Z][a-z]?(?:_[A-Za-z0-9]+)*', row['variant']):
                    raise ValueError('Artifact variant')
                if row['title'] != 'PAW_PBE ' + row['variant'] and (not isinstance(row['title'], str) or not re.fullmatch(r'PAW_PBE ' + re.escape(row['variant']) + r' (?:\d{1,2}[A-Za-z]{3}\d{2,4}|\d{4}-\d{2}-\d{2})', row['title'])):
                    raise ValueError('Artifact title')
                if any(not isinstance(row[field], str) or not _HASH.fullmatch(row[field]) for field in ('source_sha256', 'decoded_sha256')):
                    raise ValueError('Artifact row fingerprint')
            if any(not isinstance(value[field], str) or len(value[field]) > 40 or not value[field].endswith('Z')
                   for field in ('created_at', 'expires_at')):
                raise ValueError('Artifact timestamp type')
            created = datetime.fromisoformat(value['created_at'].replace('Z', '+00:00')).timestamp()
            expires = datetime.fromisoformat(value['expires_at'].replace('Z', '+00:00')).timestamp()
            if expires <= created or expires - created > self.limits.artifact_ttl + .001:
                raise ValueError('Artifact lifetime')
        except (OSError, ValueError, TypeError, KeyError, ToolboxError):
            fail('ARTIFACT_INVALID', '产物记录无法完整核验；原文件保留，请重新生成', 503, True)
        if expires <= time.time():
            fail('ARTIFACT_EXPIRED', '生成产物已过期；请重新选择并生成', 410, True)
        return stored

    def artifact(self, artifact_id):
        with self.libraries._lock:
            return {'artifact': copy.deepcopy(self._load(artifact_id)['artifact'])}

    def download(self, artifact_id):
        with self.libraries._lock:
            value = self._load(artifact_id)['artifact']
            try:
                folder = self._artifact_directory(artifact_id)
                raw = read_source(folder / 'POTCAR', folder, self._artifact_limits())
                if len(raw) != value['size_bytes'] or digest(raw) != value['sha256']:
                    raise ValueError('Artifact bytes changed')
                validation(validate_potcar, raw, tuple(row['element'] for row in value['rows']))
            except (OSError, ValueError, ToolboxError):
                fail('ARTIFACT_INVALID', '产物内容无法完整核验；请重新生成', 503, True)
            return raw

    def _artifact_limits(self):
        from dataclasses import replace
        return replace(self.limits, max_input=self.limits.max_assembly)

    def generate(self, payload):
        exact(payload, {'preview_id', 'selection_digest', 'confirmed_order_and_variants', 'idempotency_key'},
              {'preview_id', 'selection_digest', 'confirmed_order_and_variants', 'idempotency_key'})
        identifier(payload['preview_id'])
        if payload['confirmed_order_and_variants'] is not True:
            fail('CONFIRMATION_REQUIRED', '请确认本次实际物种顺序及变体')
        if not isinstance(payload['selection_digest'], str) or not _HASH.fullmatch(payload['selection_digest']):
            fail('INVALID_REQUEST', '选择指纹格式无效')
        key = payload['idempotency_key']
        if not isinstance(key, str) or not re.fullmatch(r'[\x20-\x7e]{1,128}', key):
            fail('INVALID_REQUEST', '幂等标识需为 1–128 个可打印 ASCII 字符')
        key_hash = digest(key.encode('ascii'))
        request_digest = digest(json.dumps({k: payload[k] for k in ('preview_id', 'selection_digest', 'confirmed_order_and_variants')}, sort_keys=True).encode())
        # The key determines a private server-owned directory. Persisted binding
        # and bytes publish together, so restart cannot orphan an idempotent success.
        artifact_id = key_hash[:32]
        with self.libraries._lock:
            target = self._artifact_directory(artifact_id)
            if target.exists():
                stored = self._load(artifact_id)
                if stored['key_sha256'] != key_hash or stored['request_digest'] != request_digest:
                    fail('IDEMPOTENCY_CONFLICT', '此幂等标识已绑定不同确认内容；请使用新的标识', 409)
                self.download(artifact_id)
                return {'artifact': copy.deepcopy(stored['artifact'])}
            preview = self.previews.get(payload['preview_id'])
            if preview is None:
                fail('PREVIEW_NOT_FOUND', '预览不存在或服务已重启；请重新预览', 404, True)
            if preview['expiry'] <= time.time():
                fail('PREVIEW_EXPIRED', '预览已过期；请重新预览后确认', 410, True)
            response, pin = preview['response'], preview['pin']
            if payload['selection_digest'] != response['selection_digest']:
                fail('SELECTION_DIGEST_MISMATCH', '预览选择已不匹配；请刷新后重新确认', 409, True)
            if response['blockers']:
                fail('SELECTION_BLOCKED', '仍有物种块未选择；请完成选择并刷新预览', 409, True)
            library = self.libraries._library(pin['library_id'])
            if library['source_token'] != pin['source_token']:
                fail('SOURCE_REPLACED', '库来源已替换；请重新选择并确认', 409, True)
            library, index = self._snapshot(pin['library_id'], pin['index_revision'])
            current = {row['dataset_id']: row for row in index['datasets']}
            chunks, source_rows, total = [], [], 0
            root = Path(library['root_path'])
            for selected, row in zip(pin['datasets'], response['rows']):
                if current.get(selected['dataset_id']) != selected:
                    fail('SOURCE_CHANGED', '索引中的选择已变化；请重新扫描并选择', 409, True)
                raw = read_source(root / selected['relative_path'], root, self.limits)
                if digest(raw) != selected['source_sha256']:
                    fail('SOURCE_CHANGED', '选中源文件内容已变化；请重新扫描并选择', 409, True)
                decoded, compression = decode(raw, Path(selected['relative_path']).name, self.limits)
                check = dataset(pin['library_id'], selected['relative_path'])
                metadata(decoded, check)
                if (check['status'] != 'ready' or compression != selected['compression'] or
                        digest(decoded) != selected['decoded_sha256'] or any(check[field] != selected[field] for field in _META)):
                    fail('SOURCE_CHANGED', '数据集元数据或格式与预览不符；请重新扫描并选择', 409, True)
                total += len(decoded)
                if total > self.limits.max_assembly:
                    fail('ASSEMBLY_LIMIT', '完整拼接超过 32 MiB 上限；未生成截断产物')
                chunks.append(decoded)
                source_rows.append({**{k: row[k] for k in ('position', 'element', 'atom_count', 'dataset_id')},
                                    **{k: selected[k] for k in ('variant', 'title', 'decoded_sha256', 'source_sha256')}})
            combined = b''.join(chunks)
            validation(validate_potcar, combined, tuple(pin['species']))
            epoch = time.time()
            artifact = {'artifact_id': artifact_id, 'preview_id': payload['preview_id'], 'selection_digest': payload['selection_digest'],
                        'structure_sha256': pin['structure_sha256'], 'library_id': pin['library_id'], 'index_revision': pin['index_revision'],
                        'status': 'ready', 'size_bytes': len(combined), 'sha256': digest(combined),
                        'created_at': timestamp(epoch), 'expires_at': timestamp(epoch + self.limits.artifact_ttl), 'rows': source_rows}
            temporary = Path(tempfile.mkdtemp(prefix='.assembly-', dir=self.root))
            try:
                with (temporary / 'POTCAR').open('xb') as handle:
                    handle.write(combined)
                    handle.flush()
                    os.fsync(handle.fileno())
                atomic_json(temporary / 'manifest.json', {'schema_version': 1, 'artifact': artifact,
                            'request_digest': request_digest, 'key_sha256': key_hash})
                os.rename(temporary, target)
            finally:
                # Only this newly created app-owned staging directory is removed.
                if temporary.exists():
                    shutil.rmtree(temporary)
            return {'artifact': copy.deepcopy(artifact)}
