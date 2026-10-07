"""Revision-locked registration and cancellable read-only scans.

Only complete scans publish metadata. A small rollback journal protects the
registry/index pair across failed writes or interrupted publication. Source
bytes are kept only in worker memory, never in application persistence.
"""
from __future__ import annotations

import base64
import copy
import json
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..contracts import ToolboxError
from ..storage import atomic_json, read_object
from .filesystem import (checked_path, check_work, collection_paths,
                         enumerate_candidates, fail, read_source)
from .limits import Limits
from .metadata import dataset, decode, digest, issue, mark_duplicates, metadata

ACTIVE = {'queued', 'running'}


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def exact(payload, allowed, required=()):
    if not isinstance(payload, dict) or set(payload) - set(allowed) or set(required) - set(payload):
        fail('INVALID_REQUEST', '请求字段不完整或包含不允许的字段')


def revision(value):
    if type(value) is not int or value < 0:
        fail('INVALID_REQUEST', '需要非负整数版本号')
    return value


def text(value, *, optional=False, limit=200):
    if optional and value is None:
        return None
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        fail('INVALID_REQUEST', '名称或备注格式无效或超过长度上限')
    if not optional and not value.strip():
        fail('INVALID_REQUEST', '显示名称不能为空')
    return value.strip()


def acknowledge(value):
    if not isinstance(value, dict) or set(value) != {'confirmed'} or value['confirmed'] is not True:
        fail('SOURCE_ACK_REQUIRED', '请确认有权使用所登记的本地赝势来源')
    return {'confirmed': True, 'confirmed_at': now()}


def fingerprint(rows):
    # A move keeps this exact byte fingerprint. Unreadable candidates prevent
    # attesting a move; explicit source replacement remains available.
    if not rows or any(not row['source_sha256'] for row in rows):
        return None
    value = sorted((row['relative_path'], row['source_sha256']) for row in rows)
    return digest(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


class PotcarLibraryService:
    def __init__(self, root: Path, *, limits=None):
        self.root = Path(root) / 'potcar'
        self.limits = limits or Limits()
        self._lock = threading.RLock()
        self._events = {}
        self._threads = {}
        self._closed = False
        self.root.parent.mkdir(parents=True, exist_ok=True)
        checked_path(str(self.root.parent), directory=True)
        self.root.mkdir(parents=True, exist_ok=True)
        checked_path(str(self.root), directory=True)
        (self.root / 'indexes').mkdir(exist_ok=True)
        (self.root / 'pending').mkdir(exist_ok=True)
        checked_path(str(self.root / 'indexes'), directory=True)
        checked_path(str(self.root / 'pending'), directory=True)
        self._registry_path = self.root / 'registry.json'
        if self._registry_path.exists():
            checked_path(str(self._registry_path))
        self._data = read_object(self._registry_path) if self._registry_path.is_file() else {
            'schema_version': 1, 'revision': 0, 'default_library_id': None, 'libraries': {}, 'scans': {}}
        self._validate_store()
        self._recover()
        if not self._registry_path.exists():
            atomic_json(self._registry_path, self._data)

    def _validate_store(self):
        data = self._data
        if set(data) != {'schema_version', 'revision', 'default_library_id', 'libraries', 'scans'}:
            raise ValueError('Invalid POTCAR registry fields; original preserved')
        if data.get('schema_version') != 1 or type(data.get('revision')) is not int or data['revision'] < 0:
            raise ValueError('Invalid POTCAR registry; original preserved')
        for field in ('libraries', 'scans'):
            if not isinstance(data.get(field), dict):
                raise ValueError('Invalid POTCAR registry; original preserved')
            for key, row in data[field].items():
                if not isinstance(key, str) or len(key) != 32 or any(c not in '0123456789abcdef' for c in key) or not isinstance(row, dict):
                    raise ValueError('Invalid POTCAR registry entry; original preserved')
        for key, row in data['libraries'].items():
            required = {'library_id', 'display_name', 'root_path', 'version_note', 'revision', 'source_ack', 'source_token', 'created_at', 'updated_at'}
            if set(row) != required or row.get('library_id') != key or type(row.get('revision')) is not int or row['revision'] < 1 or any(not isinstance(row.get(field), str) for field in ('root_path', 'display_name', 'source_token', 'created_at', 'updated_at')):
                raise ValueError('Invalid POTCAR library; original preserved')
            ack = row['source_ack']
            if not isinstance(ack, dict) or set(ack) != {'confirmed', 'confirmed_at'} or ack['confirmed'] is not True or not isinstance(ack['confirmed_at'], str):
                raise ValueError('Invalid POTCAR source acknowledgment; original preserved')
        if data.get('default_library_id') is not None and data['default_library_id'] not in data['libraries']:
            raise ValueError('Invalid POTCAR default library; original preserved')
        for key, scan in data['scans'].items():
            if scan.get('scan_id') != key or not isinstance(scan.get('library_id'), str) or scan.get('status') not in ACTIVE | {'succeeded', 'failed', 'cancelled'}:
                raise ValueError('Invalid POTCAR scan; original preserved')

    def _index_path(self, library_id):
        return self.root / 'indexes' / (library_id + '.json')

    def _index(self, library):
        path = self._index_path(library['library_id'])
        if not path.exists():
            return None
        checked_path(str(path))
        data = read_object(path)
        if not isinstance(data.get('source_token'), str):
            raise ValueError('Invalid POTCAR index; original preserved')
        if data.get('source_token') != library['source_token']:
            return None
        if not isinstance(data.get('datasets'), list) or type(data.get('index_revision')) is not int or not isinstance(data.get('summary'), dict) or 'source_fingerprint' not in data:
            raise ValueError('Invalid POTCAR index; original preserved')
        required = set(dataset(library['library_id'], ''))
        if any(not isinstance(row, dict) or set(row) != required or not isinstance(row['issues'], list) or
               any(not isinstance(entry, dict) or set(entry) != {'code', 'message'} or
                   not isinstance(entry['code'], str) or not isinstance(entry['message'], str) for entry in row['issues'])
               for row in data['datasets']):
            raise ValueError('Invalid POTCAR dataset; original preserved')
        return data

    def _save(self, value):
        atomic_json(self._registry_path, value)
        self._data = value

    def _recover(self):
        changed = False
        for path in (self.root / 'pending').glob('*.json'):
            journal = read_object(path)
            lid = path.stem
            if len(lid) != 32 or any(c not in '0123456789abcdef' for c in lid):
                raise ValueError('Invalid POTCAR publication journal; original preserved')
            scan = self._data['scans'].get(journal.get('scan_id'), {})
            if scan.get('status') != 'succeeded':
                if journal.get('previous') is None:
                    self._index_path(lid).unlink(missing_ok=True)
                else:
                    atomic_json(self._index_path(lid), journal['previous'])
            path.unlink()
        value = copy.deepcopy(self._data)
        for scan in value['scans'].values():
            if scan.get('status') in ACTIVE:
                scan.update(status='failed', finished_at=now(), error={
                    'code': 'POTCAR_SCAN_INTERRUPTED', 'message': '上次扫描中断；原索引保留，请重新扫描', 'retryable': True})
                changed = True
        if changed:
            value['revision'] += 1
            self._save(value)

    def _library(self, library_id):
        library = self._data['libraries'].get(library_id)
        if library is None:
            fail('LIBRARY_NOT_FOUND', '库登记不存在或已删除', 404)
        return library

    def _check_revision(self, library, expected):
        if revision(expected) != library['revision']:
            fail('REVISION_CONFLICT', '库登记已更新；请刷新后重试', 409, True)

    def _registry_revision(self, expected):
        if revision(expected) != self._data['revision']:
            fail('REVISION_CONFLICT', '库列表已更新；请刷新后重试', 409, True)

    def _active(self, library_id):
        return next((s for s in self._data['scans'].values() if s['library_id'] == library_id and s['status'] in ACTIVE), None)

    def _mutable(self, library):
        if self._active(library['library_id']) or any(
                self._data['scans'][sid]['library_id'] == library['library_id'] and thread.is_alive()
                for sid, thread in self._threads.items()):
            fail('SCAN_ACTIVE', '此库正在扫描；请等待完成或取消后重试', 409, True)

    def _public(self, library):
        value = copy.deepcopy(library)
        value.pop('source_token', None)
        try:
            checked_path(library['root_path'], directory=True)
            value['reachable'] = True
        except ToolboxError:
            value['reachable'] = False
        index = self._index(library)
        scans = [s for s in self._data['scans'].values() if s['library_id'] == library['library_id'] and s.get('source_token') == library['source_token']]
        value.update(is_default=self._data['default_library_id'] == library['library_id'],
                     index_revision=index['index_revision'] if index else None,
                     source_fingerprint=index['source_fingerprint'] if index else None,
                     summary=index['summary'] if index else None,
                     scan=self._public_scan(scans[-1]) if scans else None)
        return value

    @staticmethod
    def _public_scan(scan):
        allowed = {'scan_id', 'library_id', 'status', 'scanned_count', 'candidate_count', 'failed_count',
                   'created_at', 'finished_at', 'error', 'index_revision'}
        return {key: copy.deepcopy(value) for key, value in scan.items() if key in allowed}

    def discover(self, payload):
        exact(payload, {'root_path'}, {'root_path'})
        root = checked_path(payload['root_path'], directory=True)
        deadline = time.monotonic() + self.limits.scan_timeout
        candidates = enumerate_candidates(root, self.limits, deadline)
        if not candidates:
            fail('NO_CANDIDATES', '所选目录内没有可识别名称的 POTCAR 候选文件')
        groups = collection_paths(root, candidates)
        return {'root_path': str(root), 'requires_selection': len(groups) != 1 or groups[0][0] != root,
                'collections': [{'root_path': str(path), 'display_name': path.name, 'candidate_count': count} for path, count in groups],
                'candidate_count': len(candidates)}

    def _source(self, root_path):
        found = self.discover({'root_path': root_path})
        if found['requires_selection']:
            fail('COLLECTION_SELECTION_REQUIRED', '请选择发现结果中的具体集合；不能将父目录混合登记')
        root = Path(found['root_path'])
        # Do not register application state as its own source (or vice versa).
        state = self.root.resolve()
        if root == state or root.is_relative_to(state) or state.is_relative_to(root):
            fail('INVALID_PATH', '源库目录必须与应用状态目录分开')
        return root

    def _unique(self, root, *, exclude=None):
        for lid, row in self._data['libraries'].items():
            if lid != exclude and Path(row['root_path']) == root:
                fail('LIBRARY_EXISTS', '此具体目录已登记；请使用现有登记', 409)

    def list_libraries(self):
        with self._lock:
            return {'libraries': [self._public(row) for row in self._data['libraries'].values()],
                    'default_library_id': self._data['default_library_id'], 'revision': self._data['revision']}

    def detail(self, library_id):
        with self._lock:
            return {'library': self._public(self._library(library_id)), 'revision': self._data['revision']}

    def register(self, payload):
        exact(payload, {'display_name', 'root_path', 'version_note', 'source_ack', 'expected_registry_revision'},
              {'display_name', 'root_path', 'source_ack', 'expected_registry_revision'})
        name, note = text(payload['display_name']), text(payload.get('version_note'), optional=True, limit=1000)
        ack = acknowledge(payload['source_ack'])
        root = self._source(payload['root_path'])
        with self._lock:
            self._registry_revision(payload['expected_registry_revision'])
            self._unique(root)
            lid = uuid.uuid4().hex
            timestamp = now()
            row = {'library_id': lid, 'display_name': name, 'root_path': str(root), 'version_note': note,
                   'revision': 1, 'source_ack': ack, 'source_token': uuid.uuid4().hex,
                   'created_at': timestamp, 'updated_at': timestamp}
            value = copy.deepcopy(self._data)
            value['libraries'][lid] = row
            value['revision'] += 1
            self._save(value)
            return self.detail(lid)

    def patch(self, library_id, payload):
        exact(payload, {'expected_revision', 'display_name', 'version_note'}, {'expected_revision'})
        if set(payload) == {'expected_revision'}:
            fail('INVALID_REQUEST', '请提供需要更新的显示名称或版本备注')
        updates = {}
        if 'display_name' in payload:
            updates['display_name'] = text(payload['display_name'])
        if 'version_note' in payload:
            updates['version_note'] = text(payload['version_note'], optional=True, limit=1000)
        with self._lock:
            library = self._library(library_id)
            self._check_revision(library, payload['expected_revision'])
            self._mutable(library)
            value = copy.deepcopy(self._data)
            value['libraries'][library_id].update(updates, revision=library['revision'] + 1, updated_at=now())
            value['revision'] += 1
            self._save(value)
            return self.detail(library_id)

    def set_default(self, payload):
        exact(payload, {'library_id', 'expected_registry_revision'}, {'library_id', 'expected_registry_revision'})
        with self._lock:
            self._registry_revision(payload['expected_registry_revision'])
            lid = payload['library_id']
            if lid is not None:
                if not isinstance(lid, str):
                    fail('INVALID_REQUEST', '库标识必须为字符串或 null')
                self._library(lid)
            value = copy.deepcopy(self._data)
            value.update(default_library_id=lid, revision=value['revision'] + 1)
            self._save(value)
            return {'default_library_id': lid, 'revision': value['revision']}

    def _scan_rows(self, root, library_id, cancel=None, progress=None):
        deadline = time.monotonic() + self.limits.scan_timeout
        candidates = enumerate_candidates(root, self.limits, deadline, cancel)
        if not candidates:
            fail('NO_CANDIDATES', '所选集合内没有 POTCAR 候选；原索引保留')
        groups = collection_paths(root, candidates)
        if len(groups) != 1 or groups[0][0] != root:
            fail('COLLECTION_SELECTION_REQUIRED', '集合布局已变化；请选择具体集合后明确替换来源')
        rows = []
        if progress:
            progress(0, len(candidates), 0)
        failed = 0
        for path, _ in candidates:
            check_work(deadline, cancel)
            row = dataset(library_id, path.relative_to(root).as_posix())
            try:
                raw = read_source(path, root, self.limits)
                row['source_sha256'] = digest(raw)
                row['compression'] = 'gzip' if raw.startswith(b'\x1f\x8b') else 'Z' if raw.startswith(b'\x1f\x9d') else 'raw'
                decoded, row['compression'] = decode(raw, path.name, self.limits, timeout=deadline - time.monotonic())
                row['decoded_sha256'] = digest(decoded)
                metadata(decoded, row)
            except ToolboxError as exc:
                if exc.code in {'POTCAR_SOURCE_CHANGED', 'POTCAR_LINK_FORBIDDEN', 'POTCAR_PATH_OUTSIDE_LIBRARY'}:
                    raise
                row['issues'].append(issue(exc.code, str(exc)))
            rows.append(row)
            failed += row['status'] == 'invalid'
            if progress:
                progress(len(rows), len(candidates), failed)
        check_work(deadline, cancel)
        # Do not publish an apparently complete snapshot if candidates were
        # added/removed/replaced or a previously read file changed during scan.
        if enumerate_candidates(root, self.limits, deadline, cancel) != candidates:
            fail('SOURCE_CHANGED', '扫描期间源目录内容变化；原索引保留，请重试', 409, True)
        mark_duplicates(rows)
        return rows

    def relink(self, library_id, payload, *, replace=False):
        allowed = {'root_path', 'expected_revision'} | ({'source_ack'} if replace else set())
        exact(payload, allowed, allowed)
        ack = acknowledge(payload['source_ack']) if replace else None
        with self._lock:
            library = copy.deepcopy(self._library(library_id))
            self._check_revision(library, payload['expected_revision'])
            self._mutable(library)
            old_index = self._index(library)
            if not replace and (old_index is None or not old_index['source_fingerprint']):
                fail('SOURCE_UNVERIFIED', '尚无完整来源指纹，不能确认是同一来源；请选择替换来源', 409)
        root = self._source(payload['root_path'])
        rows = self._scan_rows(root, library_id) if not replace else None
        if not replace and fingerprint(rows) != old_index['source_fingerprint']:
            fail('SOURCE_MISMATCH', '新路径内容与原来源不一致；请明确选择替换来源并重新确认', 409)
        with self._lock:
            current = self._library(library_id)
            self._check_revision(current, payload['expected_revision'])
            self._mutable(current)
            self._unique(root, exclude=library_id)
            value = copy.deepcopy(self._data)
            value['libraries'][library_id].update(root_path=str(root), revision=current['revision'] + 1, updated_at=now())
            if replace:
                value['libraries'][library_id].update(source_token=uuid.uuid4().hex, source_ack=ack)
            value['revision'] += 1
            self._save(value)
            if replace:
                try:
                    self._index_path(library_id).unlink(missing_ok=True)
                except OSError:
                    pass  # Old source_token makes the obsolete index unreachable.
            return self.detail(library_id)

    def delete(self, library_id, expected_revision):
        with self._lock:
            library = self._library(library_id)
            self._check_revision(library, expected_revision)
            active = self._active(library_id)
            value = copy.deepcopy(self._data)
            if active:
                value['scans'][active['scan_id']].update(status='cancelled', finished_at=now(), error=None)
            del value['libraries'][library_id]
            if value['default_library_id'] == library_id:
                value['default_library_id'] = None
            value['revision'] += 1
            self._save(value)
            if active:
                self._events[active['scan_id']].set()
            try:
                self._index_path(library_id).unlink(missing_ok=True)
            except OSError:
                pass  # Orphaned application metadata is never a source file.
            return {'deleted': True, 'library_id': library_id, 'revision': value['revision']}

    def start_scan(self, library_id, payload):
        exact(payload, {'expected_revision'}, {'expected_revision'})
        with self._lock:
            if self._closed:
                fail('SERVICE_CLOSED', '服务正在关闭；请稍后重试', 503, True)
            library = self._library(library_id)
            self._check_revision(library, payload['expected_revision'])
            self._mutable(library)
            if len(self._threads) >= self.limits.max_parallel_scans:
                fail('SCAN_BUSY', '后台扫描数量达到上限，请稍后重试', 409, True)
            sid = uuid.uuid4().hex
            scan = {'scan_id': sid, 'library_id': library_id, 'source_token': library['source_token'], 'status': 'queued', 'scanned_count': 0,
                    'candidate_count': 0, 'failed_count': 0, 'created_at': now(), 'finished_at': None,
                    'error': None, 'index_revision': None}
            value = copy.deepcopy(self._data)
            value['scans'][sid] = scan
            value['libraries'][library_id].update(revision=library['revision'] + 1, updated_at=now())
            value['revision'] += 1
            self._save(value)
            event = threading.Event()
            self._events[sid] = event
            thread = threading.Thread(target=self._run_scan, args=(sid, copy.deepcopy(value['libraries'][library_id]), event),
                                      name='potcar-scan-' + sid[:8], daemon=True)
            self._threads[sid] = thread
            result = {'scan': self._public_scan(scan), **self.detail(library_id)}
            thread.start()
            return result

    def _progress(self, sid, scanned, candidates, failed):
        with self._lock:
            scan = self._data['scans'][sid]
            if scan['status'] in ACTIVE:
                scan.update(scanned_count=scanned, candidate_count=candidates, failed_count=failed)

    def _run_scan(self, sid, library, event):
        try:
            with self._lock:
                if self._data['scans'][sid]['status'] not in ACTIVE:
                    return
                value = copy.deepcopy(self._data)
                value['scans'][sid]['status'] = 'running'
                self._save(value)
            rows = self._scan_rows(Path(library['root_path']), library['library_id'], event,
                                   lambda *args: self._progress(sid, *args))
            with self._lock:
                current = self._data['libraries'].get(library['library_id'])
                if event.is_set() or self._closed or current is None or self._data['scans'][sid]['status'] not in ACTIVE:
                    return
                if current['revision'] != library['revision'] or current['source_token'] != library['source_token']:
                    fail('REVISION_CONFLICT', '扫描期间库登记已更新；原索引保留', 409, True)
                self._publish(sid, current, rows)
        except BaseException as exc:
            with self._lock:
                scan = self._data['scans'].get(sid)
                if scan and scan['status'] in ACTIVE:
                    error = exc if isinstance(exc, ToolboxError) else ToolboxError('POTCAR_SCAN_FAILED', '扫描或索引保存失败；原索引保留，请重试', 400, True)
                    value = copy.deepcopy(self._data)
                    value['scans'][sid].update(status='cancelled' if event.is_set() else 'failed',
                                              finished_at=now(), error=None if event.is_set() else error.payload())
                    try:
                        self._save(value)
                    except OSError:
                        # Persistence failures remain visible in this process;
                        # on restart the persisted active scan becomes interrupted.
                        self._data = value
        finally:
            with self._lock:
                self._events.pop(sid, None)
                self._threads.pop(sid, None)

    def _publish(self, sid, library, rows):
        lid = library['library_id']
        previous = self._index(library)
        index_revision = (previous['index_revision'] if previous else 0) + 1
        counts = Counter(row['status'] for row in rows)
        index = {'schema_version': 1, 'library_id': lid, 'source_token': library['source_token'],
                 'index_revision': index_revision, 'scan_id': sid, 'created_at': now(),
                 'source_fingerprint': fingerprint(rows), 'datasets': rows,
                 'summary': {'total': len(rows), **{key: counts[key] for key in ('ready', 'unsupported', 'invalid', 'ambiguous')}}}
        journal = self.root / 'pending' / (lid + '.json')
        atomic_json(journal, {'scan_id': sid, 'previous': previous})
        try:
            atomic_json(self._index_path(lid), index)
            value = copy.deepcopy(self._data)
            value['scans'][sid].update(status='succeeded', finished_at=now(), index_revision=index_revision,
                                       scanned_count=len(rows), candidate_count=len(rows), failed_count=counts['invalid'])
            value['libraries'][lid].update(revision=library['revision'] + 1, updated_at=now())
            value['revision'] += 1
            self._save(value)
        except BaseException:
            if previous is None:
                self._index_path(lid).unlink(missing_ok=True)
            else:
                atomic_json(self._index_path(lid), previous)
            journal.unlink(missing_ok=True)
            raise
        journal.unlink(missing_ok=True)

    def scan(self, scan_id):
        with self._lock:
            scan = self._data['scans'].get(scan_id)
            if scan is None:
                fail('SCAN_NOT_FOUND', '扫描记录不存在', 404)
            return {'scan': self._public_scan(scan)}

    def cancel(self, scan_id):
        with self._lock:
            scan = self.scan(scan_id)['scan']
            if scan['status'] in ACTIVE:
                value = copy.deepcopy(self._data)
                value['scans'][scan_id].update(status='cancelled', finished_at=now(), error=None)
                value['revision'] += 1
                self._save(value)
                self._events[scan_id].set()
            return self.scan(scan_id)

    def datasets(self, library_id, *, element=None, status=None, cursor=None, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            fail('INVALID_REQUEST', '分页大小必须在 1 到 100 之间')
        if element is not None and (not isinstance(element, str) or len(element) > 3):
            fail('INVALID_REQUEST', '元素过滤条件无效')
        if status is not None and status not in {'ready', 'unsupported', 'invalid', 'ambiguous'}:
            fail('INVALID_REQUEST', '候选状态过滤条件无效')
        with self._lock:
            library = self._library(library_id)
            index = self._index(library)
            index_revision = index['index_revision'] if index else None
            offset = 0
            if cursor is not None:
                try:
                    if not isinstance(cursor, str) or len(cursor) > 1024:
                        raise ValueError()
                    pin = json.loads(base64.urlsafe_b64decode(cursor.encode('ascii')).decode('utf-8'))
                    if not isinstance(pin, list) or len(pin) != 6 or type(pin[5]) is not int or pin[5] < 0:
                        raise ValueError()
                except (ValueError, UnicodeError, TypeError):
                    fail('INVALID_CURSOR', '分页游标无效，请重新加载列表')
                if pin[:5] != [library_id, library['source_token'], index_revision, element, status]:
                    fail('INDEX_REVISION_CONFLICT', '索引或过滤条件已变化；请重新加载第一页', 409, True)
                offset = pin[5]
            rows = [row for row in (index['datasets'] if index else [])
                    if (element is None or row['element'] == element) and (status is None or row['status'] == status)]
            end = offset + limit
            next_cursor = None
            if end < len(rows):
                pin = [library_id, library['source_token'], index_revision, element, status, end]
                next_cursor = base64.urlsafe_b64encode(json.dumps(pin, separators=(',', ':')).encode('utf-8')).decode('ascii')
            return {'index_revision': index_revision, 'datasets': copy.deepcopy(rows[offset:end]),
                    'next_cursor': next_cursor, 'total': len(rows)}

    def close(self):
        with self._lock:
            self._closed = True
            threads = list(self._threads.values())
            for sid in list(self._events):
                try:
                    self.cancel(sid)
                except OSError:
                    self._events[sid].set()
                    self._data['scans'][sid].update(status='cancelled', finished_at=now(), error=None)
        # Decode loops have deadlines. This short bounded join does not hold the
        # registry lock or wait for arbitrary filesystem latency.
        deadline = time.monotonic() + min(self.limits.decode_timeout + 1, 12)
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
