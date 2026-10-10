"""Exact submitted task results -> bounded, immutable, offline PP snapshots."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
import re
import shutil
import threading
import time
import uuid

from .. import consent
from ..computation import digest
from ..contracts import ToolboxError
from ..results import submitted_snapshot
from ..ssh.errors import SSHError
from ..ssh.remote_files import RemoteFileError
from .store import MAX_BATCH, MAX_FILE, MAX_STORE, NAMES, atomic, fail

PREVIEW_LIFETIME = 15 * 60
DOWNLOAD_TIMEOUT = 240


def now():
    return datetime.now(timezone.utc).isoformat()


def remote_error(exc):
    if isinstance(exc, ToolboxError):
        return exc
    code = exc.code if isinstance(exc, RemoteFileError) else 'PP_TRANSPORT_UNAVAILABLE'
    status = (403 if code in {'CONTENT_READ_DENIED', 'PATH_SYMLINK_ESCAPE'} else
              404 if code == 'SOURCE_NOT_FOUND' else
              413 if code in {'PP_TOO_LARGE', 'RESULT_TOO_LARGE'} else
              503 if code in {'PP_TRANSPORT_UNAVAILABLE', 'REMOTE_CAPABILITY_UNAVAILABLE',
                              'PROTOCOL_ERROR', 'PP_DOWNLOAD_TIMEOUT', 'REMOTE_IO_ERROR'} else 409)
    return ToolboxError(code, '任务结果读取失败：' + code, status, retryable=status == 503)


def required_files(kind, names):
    names = set(names)
    if not names or names - NAMES:
        fail('只可选择预览内的受支持原始文件；不接收 POTCAR、WAVECAR 或任意路径')
    if 'vasprun.xml' in names and names & {'DOSCAR', 'EIGENVAL'}:
        fail('请选择 XML 或文本路线，避免多个结果主文件混用')
    if {'POSCAR', 'CONTCAR'} <= names:
        fail('请选择一份匹配的结构文件，避免 POSCAR／CONTCAR 歧义')
    required = ({'vasprun.xml'} | ({'KPOINTS'} if kind == 'band' else set())
                if 'vasprun.xml' in names else
                {'DOSCAR', 'INCAR'} if kind == 'dos' else {'EIGENVAL', 'INCAR', 'KPOINTS'})
    missing = required - names
    if kind == 'band' and 'vasprun.xml' not in names and not names & {'POSCAR', 'CONTCAR'}:
        missing.add('POSCAR（或 CONTCAR）')
    if missing:
        fail('所选路线缺少：' + '、'.join(sorted(missing)), 'PP_MISSING_SOURCE')


class TaskSources:
    def __init__(self, service, store):
        self.service, self.store = service, store
        self.previews = store.root / 'previews'
        self.previews.mkdir(exist_ok=True)
        self.active = {}
        self.closed = False

    def _snapshot(self, identity):
        with consent.task_lock(identity['project_id'], identity['task_id']):
            return submitted_snapshot(self.service, **identity)

    def _remote_target(self, files, snapshot):
        target = {**snapshot['target'], 'identity_file': snapshot['target']['identity_file'] or None,
                  'known_hosts_path': snapshot['target']['known_hosts_path'] or None}
        if files.scheduler_target != target:
            fail('SSH 目标与本次提交不同', 'RESULT_ENDPOINT_CHANGED', 409)

    def preview(self, identity, kind):
        before = self._snapshot(identity)
        try:
            with self.service.files.remote() as files:
                self._remote_target(files, before)
                observation = files.preview_pp(before['directory'])
        except (RemoteFileError, SSHError) as exc:
            raise remote_error(exc) from exc
        if self._snapshot(identity) != before:
            fail('任务在预览期间变化，请重新选择本次尝试', 'RESULT_ATTEMPT_CHANGED', 409)
        available = {f['name'] for f in observation['files'] if f['available']}
        suggested = (['vasprun.xml'] if 'vasprun.xml' in available else
                     ['DOSCAR', 'INCAR'] if kind == 'dos' else
                     ['EIGENVAL', 'INCAR', 'KPOINTS', 'POSCAR' if 'POSCAR' in available else 'CONTCAR'])
        if kind == 'band' and 'vasprun.xml' in available:
            suggested.append('KPOINTS')
        warnings = []
        missing = [name for name in suggested if name not in available]
        if missing:
            warnings.append('推荐路线缺少可用文件：' + '、'.join(missing))
        warnings.append('能带需要同次计算的 line-mode KPOINTS；文件可下载不代表数据格式或科学收敛已验收。'
                        if kind == 'band' else 'DOSCAR 仅支持总 DOS；投影 DOS 请使用 vasprun.xml。')
        for file in observation['files']:
            if file.get('reason') in {'PP_TOO_LARGE', 'PATH_SYMLINK_ESCAPE', 'CONTENT_READ_DENIED', 'PP_EMPTY_FILE'}:
                warnings.append(file['name'] + ' 不可导入：' + file['reason'])
        public = {'id': 'ppv_' + uuid.uuid4().hex, 'kind': kind,
                  'source': {'kind': 'task_result', **identity,
                             'submission_action_id': before['job']['submission_action_id'],
                             'slurm_id': before['job']['slurm_id'], 'remote_directory': before['directory'],
                             'scheduler_target': before['target'],
                             'submission_binding_sha256': before['action']['binding_hash']},
                  'files': [{k: v for k, v in f.items() if k != 'metadata'} for f in observation['files']],
                  'suggested_files': [n for n in suggested if n in available], 'warnings': warnings,
                  'limits': {'max_file_bytes': MAX_FILE, 'max_total_bytes': MAX_BATCH},
                  'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=PREVIEW_LIFETIME)).isoformat()}
        private = {'public': public, 'identity': identity, 'snapshot': before, 'observation': observation}
        with self.store.guard:
            # Previews are small metadata only; retain at most 100 unexpired receipts.
            receipts = list(self.previews.glob('ppv_*.json'))
            for receipt in receipts:
                try:
                    if datetime.fromisoformat(json.loads(receipt.read_text(encoding='utf-8'))['public']['expires_at']) < datetime.now(timezone.utc):
                        receipt.unlink()
                except (OSError, ValueError, KeyError):
                    continue
            if len(list(self.previews.glob('ppv_*.json'))) >= 100:
                fail('预览记录已达上限，请稍后重试', 'PP_BUSY', 429)
            atomic(self.previews / (public['id'] + '.json'), private)
        return public

    def _read_preview(self, ident):
        if not re.fullmatch(r'ppv_[a-f0-9]{32}', ident or ''):
            fail('任务预览不存在', 'PP_PREVIEW_NOT_FOUND', 404)
        try:
            return json.loads((self.previews / (ident + '.json')).read_text(encoding='utf-8'))
        except FileNotFoundError:
            fail('任务预览不存在，请重新预览', 'PP_PREVIEW_NOT_FOUND', 404)

    def _check_receipt(self, recipe):
        if datetime.fromisoformat(recipe['public']['expires_at']) <= datetime.now(timezone.utc):
            fail('预览已过期，请重新预览并确认下载', 'PP_PREVIEW_EXPIRED', 409)
        if self._snapshot(recipe['identity']) != recipe['snapshot']:
            fail('任务尝试或提交来源已变化，请重新预览', 'RESULT_ATTEMPT_CHANGED', 409)

    def reuse(self, ident):
        with self.store.guard:
            doc = self.store.read(ident)
            if doc['source']['kind'] != 'task_result' or (doc.get('download') or {}).get('status') != 'cached':
                fail('该任务缓存尚未完整保存', 'PP_CACHE_INCOMPLETE', 409)
            self.store.verify_sources(doc)
            return doc

    def import_files(self, preview_id, names, title):
        recipe = self._read_preview(preview_id)
        self._check_receipt(recipe)
        if not isinstance(names, list) or len(names) != len(set(names)) or len(names) > len(NAMES):
            fail('请选择预览内不重复的文件')
        required_files(recipe['public']['kind'], names)
        allowed = {f['name']: f for f in recipe['observation']['files'] if f['available']}
        if any(n not in allowed for n in names):
            fail('所选文件不可用，请重新预览', 'PP_SOURCE_UNAVAILABLE', 409)
        size = sum(allowed[n]['size_bytes'] for n in names)
        if size > MAX_BATCH:
            fail('一批任务结果最多 128 MiB', 'PP_TOO_LARGE', 413)
        recipe['selected_files'] = list(names)
        with self.store.guard:
            self._available(size)
            doc = self.store.create(recipe['public']['kind'], title)
            doc['source'] = {**copy.deepcopy(recipe['public']['source']), 'preview_id': preview_id}
            atomic(self.store.directory(doc['id']) / 'task-source.json', recipe)
            return self._start(doc, size)

    def _available(self, size):
        if self.closed or len(self.active) >= 2:
            fail('任务下载正在处理，请稍后重试', 'PP_BUSY', 409)
        datasets = self.store.list()
        downloading = [d['download'] for d in datasets if d['status'] == 'downloading' or
                       (d['id'] in self.active and d.get('download', {}).get('status') != 'cached')]
        reserved = sum(d['total_bytes'] for d in downloading)
        total = sum(f['size_bytes'] for d in datasets for f in d['files'])
        if total + reserved + size + len(self.store.uploads) * MAX_FILE > MAX_STORE:
            fail('本地原始快照超过 1 GiB 上限，请删除不需要的分析', 'PP_QUOTA', 413)
        try:
            free_bytes = shutil.disk_usage(self.store.root).free
        except OSError:
            fail('无法核对本地可用空间，请检查快照目录后重试', 'PP_STORAGE_UNAVAILABLE', 503)
        outstanding = sum(max(0, d['total_bytes'] - d['completed_bytes']) for d in downloading)
        if free_bytes < size + outstanding + len(self.store.uploads) * MAX_FILE:
            fail('本地可用空间不足以保存本批原始结果，请释放空间后重试', 'PP_DISK_FULL', 413)

    def retry(self, ident):
        with self.store.guard:
            doc = self.store.read(ident)
            if ident in self.active:
                fail('原下载正在结束并清理临时数据，请稍后重试', 'PP_BUSY', 409)
            if doc['source']['kind'] != 'task_result' or (doc.get('download') or {}).get('status') not in {'failed', 'cancelled'}:
                fail('当前快照不能重试下载，完整缓存请直接解析', 'PP_BUSY', 409)
            recipe = json.loads((self.store.directory(ident) / 'task-source.json').read_text(encoding='utf-8'))
            self._check_receipt(recipe)
            size = sum(f['size_bytes'] for f in recipe['observation']['files'] if f['name'] in recipe['selected_files'])
            self._available(size)
            return self._start(doc, size)

    def _start(self, doc, size):
        ident = doc['id']
        recipe = json.loads((self.store.directory(ident) / 'task-source.json').read_text(encoding='utf-8'))
        event = threading.Event()
        doc.update(status='downloading', error=None, download={'status': 'downloading', 'completed_bytes': 0,
                   'total_bytes': size, 'current_file': None, 'completed_files': 0,
                   'total_files': len(recipe['selected_files']), 'error': None})
        self.store.save(doc)
        thread = threading.Thread(target=self._run, args=(ident, recipe, event), name='pp-download-' + ident, daemon=True)
        self.active[ident] = (event, thread)
        thread.start()
        return doc

    def _observation(self, files, recipe, event):
        self._remote_target(files, recipe['snapshot'])
        current = files.preview_pp(recipe['snapshot']['directory'], expected=recipe['observation'], should_cancel=event.is_set)
        expected = recipe['observation']
        selected = recipe['selected_files']
        # Parent directories may acquire unrelated siblings. Compare their identities,
        # while keeping this result directory and selected file metadata fully stable.
        current_root, expected_root = current['root'], expected['root']
        ancestors = lambda root: [{key: node.get(key) for key in ('path', 'device', 'inode', 'type')}
                                  for node in root['ancestors']]
        root_matches = ({k: v for k, v in current_root.items() if k != 'ancestors'} ==
                        {k: v for k, v in expected_root.items() if k != 'ancestors'} and
                        ancestors(current_root) == ancestors(expected_root))
        if (current['endpoint'] != expected['endpoint'] or not root_matches
                or [f for f in current['files'] if f['name'] in selected] !=
                   [f for f in expected['files'] if f['name'] in selected]):
            fail('任务结果已变化，请重新预览并建立新快照', 'PP_SOURCE_CHANGED', 409)

    def _run(self, ident, recipe, event):
        directory = self.store.directory(ident)
        staged = []
        try:
            self._check_receipt(recipe)
            deadline = time.monotonic() + DOWNLOAD_TIMEOUT
            with self.service.files.remote() as files:
                if event.is_set():
                    return
                self._observation(files, recipe, event)
                completed = 0
                for index, name in enumerate(recipe['selected_files']):
                    if event.is_set():
                        return
                    path = directory / (name + '.download')
                    path.unlink(missing_ok=True)
                    def progress(length, name=name, index=index, base=completed):
                        with self.store.guard:
                            if event.is_set():
                                return
                            doc = self.store.read(ident)
                            doc['download'].update(completed_bytes=base + length, current_file=name, completed_files=index)
                            self.store.save(doc)
                    progress(0)
                    with path.open('xb') as target:
                        length, checksum = files.stream_pp(recipe['observation'], name, target,
                                                           should_cancel=event.is_set, progress=progress, deadline=deadline)
                    actual = self.store.file_fingerprint(path)
                    if actual != (length, checksum):
                        fail('下载校验未通过，未保存原始快照', 'PP_SOURCE_CHANGED', 409)
                    completed += length
                    staged.append({'name': name, 'role': name, 'size_bytes': length, 'sha256': checksum,
                                   'remote_path': recipe['snapshot']['directory'] + '/' + name,
                                   'remote_metadata': next(f['metadata'] for f in recipe['observation']['files'] if f['name'] == name)})
                self._observation(files, recipe, event)
            self._check_receipt(recipe)
            with self.store.guard:
                if event.is_set():
                    return
                doc = self.store.read(ident)
                for file in staged:
                    os.replace(directory / (file['name'] + '.download'), directory / file['name'])
                doc['files'] = staged
                doc['source']['cached_at'] = now()
                doc['source']['snapshot_sha256'] = digest({'source': recipe['public']['source'], 'files': staged})
                doc.update(status='draft', error=None)
                doc['download'].update(status='cached', completed_bytes=completed, completed_files=len(staged), current_file=None, error=None)
                self.store.save(doc)
        except Exception as exc:
            with self.store.guard:
                doc = self.store.read(ident)
                if not event.is_set():
                    error = (remote_error(exc) if isinstance(exc, (ToolboxError, SSHError)) else
                             ToolboxError('PP_STORAGE_FAILED', '下载无法保存，请检查本地空间后重试', 500)).payload()
                    doc.update(status='failed', error=error)
                    doc['download'].update(status='failed', current_file=None, error=error)
                    self.store.save(doc)
        finally:
            with self.store.guard:
                for path in directory.glob('*.download'):
                    path.unlink(missing_ok=True)
                doc = self.store.read(ident)
                if doc.get('download', {}).get('status') != 'cached':
                    # No partial batch becomes parser input, including a failed publication.
                    for name in recipe['selected_files']:
                        (directory / name).unlink(missing_ok=True)
                self.active.pop(ident, None)

    def cancel(self, ident):
        with self.store.guard:
            doc = self.store.read(ident)
            if doc['status'] == 'downloading':
                if ident in self.active:
                    self.active[ident][0].set()
                doc.update(status='cancelled', error=None)
                doc['download'].update(status='cancelled', current_file=None, error=None)
                self.store.save(doc)
            return doc

    def close(self):
        with self.store.guard:
            self.closed = True
            threads = [value[1] for value in self.active.values()]
            for ident in list(self.active):
                self.cancel(ident)
        # SSH connection stages can precede the cancel-aware helper channel.
        # The PP-owned manager caps each at five seconds; give it time to unwind.
        deadline = time.monotonic() + 23
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
