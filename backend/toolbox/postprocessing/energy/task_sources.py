"""Exact submitted attempts -> stable whole OUTCAR -> immutable offline sample."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import threading
import time
import uuid

from ... import consent
from ...results import submitted_snapshot
from ...contracts import ToolboxError
from ...ssh.errors import SSHError
from ...ssh.remote_files import RemoteFileError
from ..task_sources import remote_error
from ..store import atomic, fail
from .calculation import fingerprint
from .store import now

STATES = frozenset({'queued', 'running', 'completed', 'failed', 'not_converged', 'cancelled'})


class EnergyTaskSources:
    def __init__(self, service, store):
        self.service, self.store = service, store
        self.previews = store.root / 'previews'
        self.previews.mkdir(exist_ok=True)
        self.slots = threading.BoundedSemaphore(2)
        self.active = set()
        self.closed = False

    def snapshot(self, identity):
        with consent.task_lock(identity['project_id'], identity['task_id']):
            return submitted_snapshot(self.service, **identity, allowed_states=STATES)

    @staticmethod
    def identity_signature(snapshot):
        # Monitor status may legitimately advance, but submitted origin cannot.
        return {**snapshot, 'job': {k: snapshot['job'].get(k) for k in
                ('key', 'attempt_id', 'submission_state', 'slurm_id', 'scheduler_target',
                 'submission_action_id', 'draft', 'precheck')}}

    def check_identity(self, recipe):
        if self.identity_signature(self.snapshot(recipe['identity'])) != self.identity_signature(recipe['snapshot']):
            fail('当前尝试或提交身份改变，请重新预览', 'RESULT_ATTEMPT_CHANGED', 409)

    @staticmethod
    def check_target(files, snapshot):
        target = {**snapshot['target'], 'identity_file': snapshot['target']['identity_file'] or None,
                  'known_hosts_path': snapshot['target']['known_hosts_path'] or None}
        if files.scheduler_target != target:
            fail('SSH目标与本次提交不同', 'RESULT_ENDPOINT_CHANGED', 409)

    def preview(self, identity):
        snapshot = self.snapshot(identity)
        try:
            with self.service.files.remote() as files:
                self.check_target(files, snapshot)
                observation = files.preview_energy(snapshot['directory'])
        except (RemoteFileError, SSHError) as exc:
            raise remote_error(exc) from exc
        recipe = {'identity': identity, 'snapshot': snapshot, 'observation': observation}
        self.check_identity(recipe)
        source = {'kind': 'task_result', **identity,
                  'submission_action_id': snapshot['job']['submission_action_id'],
                  'slurm_id': snapshot['job']['slurm_id'], 'remote_directory': snapshot['directory'],
                  'scheduler_target': snapshot['target'], 'task_status_at_preview': snapshot['job']['status']}
        public = {'id': 'epv_' + uuid.uuid4().hex, 'source': source,
                  'files': [{k: v for k, v in f.items() if k != 'metadata'} for f in observation['files']],
                  'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                  'warnings': ['仅保存完整且传输前后稳定的OUTCAR；运行中持续写入时需稍后重取。',
                               '任务状态、文件完成和科学收敛分别记录，均不会自动确认纳入。']}
        recipe['public'] = public
        self.store.write_json(self.previews / (public['id'] + '.json'), recipe)
        return public

    def import_source(self, ident, revision, preview_id, name):
        import json
        import re
        if not re.fullmatch(r'epv_[a-f0-9]{32}', preview_id):
            fail('任务预览不存在', 'ENERGY_PREVIEW_NOT_FOUND', 404)
        try:
            recipe = json.loads((self.previews / (preview_id + '.json')).read_text(encoding='utf-8'))
        except FileNotFoundError:
            fail('任务预览不存在', 'ENERGY_PREVIEW_NOT_FOUND', 404)
        if datetime.fromisoformat(recipe['public']['expires_at']) <= datetime.now(timezone.utc):
            fail('任务预览已过期，请重取', 'ENERGY_PREVIEW_EXPIRED', 409)
        selected = next((f for f in recipe['observation']['files'] if f['name'] == 'OUTCAR' and f['available']), None)
        if not selected:
            fail('OUTCAR不可下载', 'ENERGY_SOURCE_UNAVAILABLE', 409)
        self.check_identity(recipe)
        if self.closed or not self.slots.acquire(blocking=False):
            fail('能量任务下载正在处理，请稍后重试', 'ENERGY_BUSY', 409)
        event = threading.Event()
        path = None
        with self.store.guard:
            self.active.add(event)
        try:
            path, remaining = self.store.reserve(ident, revision)
            if selected['size_bytes'] > remaining:
                fail('完整OUTCAR超过导入预算', 'ENERGY_TOO_LARGE', 413)
            try:
                with self.service.files.remote() as files:
                    self.check_target(files, recipe['snapshot'])
                    self.check_observation(files, recipe, event)
                    with path.open('xb') as target:
                        length, sha = files.stream_energy(recipe['observation'], 'OUTCAR', target,
                                should_cancel=event.is_set, progress=lambda value: None, deadline=time.monotonic() + 240)
                    self.check_observation(files, recipe, event)
            except (RemoteFileError, SSHError) as exc:
                raise remote_error(exc) from exc
            self.check_identity(recipe)
            if event.is_set():
                fail('下载已中断，不保存部分来源', 'ACTION_ABORTED', 409)
            if self.store.file_fingerprint(path) != (length, sha):
                fail('下载字节长度或SHA校验失败', 'ENERGY_SOURCE_CHANGED', 409)
            source = {**copy.deepcopy(recipe['public']['source']), 'preview_id': preview_id,
                      'remote_metadata': selected['metadata'], 'cached_at': now(),
                      'snapshot_sha256': fingerprint({'identity': self.identity_signature(recipe['snapshot']),
                                                      'remote_metadata': selected['metadata'], 'sha256': sha})}
            return self.store.import_path(ident, revision, path, name, task_source=source)
        finally:
            if path:
                self.store.release(path)
            with self.store.guard:
                self.active.discard(event)
            self.slots.release()

    def check_observation(self, files, recipe, event):
        current = files.preview_energy(recipe['snapshot']['directory'], expected=recipe['observation'], should_cancel=event.is_set)
        if current != recipe['observation']:
            fail('任务文件在传输前后改变，请重取快照', 'ENERGY_SOURCE_CHANGED', 409)

    def close(self):
        with self.store.guard:
            self.closed = True
            for event in self.active:
                event.set()
