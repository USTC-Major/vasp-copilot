"""Durable snapshots and a single disposable local parser worker."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone

from ..contracts import ToolboxError
from .plotting import (VIEW_VERSION, DEFAULT_ENERGY_WINDOWS, bounds_warning, energy_bounds,
                       fit_axes, prepare_curves, upgrade_view, validate_axes, validate_energy_window)

MAX_FILE = 64 * 1024 * 1024
MAX_BATCH = 128 * 1024 * 1024
MAX_STORE = 1024 * 1024 * 1024
MAX_RESPONSE_VALUES = 50_000
NAMES = {'vasprun.xml', 'DOSCAR', 'EIGENVAL', 'KPOINTS', 'POSCAR', 'CONTCAR', 'INCAR'}


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def fail(message, code='PP_INVALID_REQUEST', status=400):
    raise ToolboxError(code, message, status)


class AnalysisStore:
    def __init__(self, root):
        self.root = Path(root) / 'postprocessing'
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = threading.RLock()
        self.uploads = set()
        self.active = None
        self.process = None
        self.thread = None
        self.task_sources = None
        for directory in self.root.glob('ds_*'):
            if not directory.is_dir():
                continue
            for temporary in directory.glob('*.upload'):
                temporary.unlink(missing_ok=True)
            for temporary in directory.glob('*.download'):
                temporary.unlink(missing_ok=True)
            try:
                doc = self.read(directory.name)
                if doc['status'] == 'processing':
                    doc.update(status='failed', error={'code': 'PP_INTERRUPTED', 'message': '上次解析被中断，可重新解析原快照'})
                    self.save(doc)
                if doc['status'] == 'downloading':
                    error = {'code': 'PP_DOWNLOAD_INTERRUPTED', 'message': '上次下载被中断；可核对原预览重试，或重新预览创建新快照'}
                    doc.update(status='failed', error=error)
                    doc['download'].update(status='failed', current_file=None, error=error)
                    self.save(doc)
                if doc['source']['kind'] == 'task_result' and doc.get('download', {}).get('status') != 'cached':
                    for name in NAMES:
                        (directory / name).unlink(missing_ok=True)
            except (OSError, ValueError):
                continue

    def directory(self, ident):
        if not re.fullmatch(r'ds_[a-f0-9]{32}', ident):
            fail('分析不存在', 'PP_NOT_FOUND', 404)
        directory = self.root / ident
        if not directory.is_dir() or directory.is_symlink():
            fail('分析不存在', 'PP_NOT_FOUND', 404)
        return directory

    def read(self, ident):
        try:
            with self.guard:
                doc = json.loads((self.directory(ident) / 'metadata.json').read_text(encoding='utf-8'))
                # Migrate a ready view once, without rewriting its scientific results.
                # The revision protects pages opened before this persistent migration.
                if doc['status'] == 'ready' and (doc['view'].get('version') != VIEW_VERSION or not doc['view'].get('axes')):
                    result = json.loads((self.directory(ident) / 'result.json').read_text(encoding='utf-8'))
                    doc['view'] = upgrade_view(result, doc['view'])
                    self.save(doc)
                return doc
        except FileNotFoundError:
            fail('分析不存在', 'PP_NOT_FOUND', 404)

    def save(self, doc):
        doc['revision'] += 1
        atomic(self.directory(doc['id']) / 'metadata.json', doc)

    def list(self):
        with self.guard:
            result = []
            for path in self.root.glob('ds_*/metadata.json'):
                try:
                    result.append(self.read(path.parent.name))
                except (OSError, ValueError, ToolboxError):
                    continue
            return sorted(result, key=lambda d: d['created_at'], reverse=True)

    def create(self, kind, title):
        if kind not in ('dos', 'band') or not isinstance(title, str) or len(title) > 120:
            fail('分析类型或名称无效')
        with self.guard:
            if len(self.list()) >= 100:
                fail('已有 100 个本地分析，请删除不需要的快照后继续', 'PP_QUOTA', 413)
            ident = 'ds_' + uuid.uuid4().hex
            (self.root / ident).mkdir()
            doc = {'id': ident, 'schema_version': 'pp.v1', 'title': title.strip() or ('DOS 分析' if kind == 'dos' else '能带分析'),
                   'kind': kind, 'source': {'kind': 'local_upload'}, 'status': 'draft', 'revision': 0,
                   'created_at': datetime.now(timezone.utc).isoformat(), 'files': [], 'error': None,
                    'view': {'version': VIEW_VERSION, 'reference': 'raw', 'reference_ev': 0.0, 'mirror_down': False,
                             'atoms': [], 'orbitals': [], 'elements': [], 'projection_grouping': 'element',
                             'energy_min_ev': DEFAULT_ENERGY_WINDOWS[kind][0], 'energy_max_ev': DEFAULT_ENERGY_WINDOWS[kind][1],
                             'band_start': 1, 'band_end': 20}, 'summary': None}
            self.save(doc)
            return doc

    def reserve_upload(self, ident, name):
        with self.guard:
            doc = self.read(ident)
            if name not in NAMES:
                fail('请选择原始 VASP 文件名；不接收 POTCAR、压缩包或任意文件')
            if doc['source']['kind'] != 'local_upload' or doc['status'] != 'draft' or ident in self.uploads:
                fail('当前快照不可写或已有上传正在进行', 'PP_CONFLICT', 409)
            if any(f['name'] == name for f in doc['files']):
                fail('同名文件已上传；请重新建立数据集，避免混用来源', 'PP_CONFLICT', 409)
            total = sum(f['size_bytes'] for d in self.list() for f in d['files'])
            pending = self.task_sources.active if self.task_sources else {}
            reserved = sum((d.get('download') or {}).get('total_bytes', 0) for d in self.list()
                           if d['status'] == 'downloading' or (d['id'] in pending and d.get('download', {}).get('status') != 'cached'))
            if total + reserved + (len(self.uploads) + 1) * MAX_FILE > MAX_STORE:
                fail('原始文件快照接近 1 GiB 上限，请先删除不需要的分析', 'PP_QUOTA', 413)
            self.uploads.add(ident)
            return self.directory(ident) / (name + '.upload'), MAX_BATCH - sum(f['size_bytes'] for f in doc['files'])

    def finish_upload(self, ident, name, size, digest):
        with self.guard:
            doc = self.read(ident)
            path = self.directory(ident)
            os.replace(path / (name + '.upload'), path / name)
            doc['files'].append({'name': name, 'role': name, 'size_bytes': size, 'sha256': digest})
            self.save(doc)
            return doc

    def start(self, ident):
        with self.guard:
            doc = self.read(ident)
            if self.active or ident in self.uploads or doc['status'] not in ('draft', 'failed', 'cancelled'):
                fail('已有解析运行，或当前数据集不能重新解析', 'PP_BUSY', 409)
            if not doc['files']:
                fail('请先选择文件')
            if doc['source']['kind'] == 'task_result' and doc.get('download', {}).get('status') != 'cached':
                fail('任务快照未完整缓存，请先重试下载', 'PP_CACHE_INCOMPLETE', 409)
            # Avoid ambiguous precedence when users select a whole mixed directory.
            names = {f['name'] for f in doc['files']}
            if 'vasprun.xml' in names and names & {'DOSCAR', 'EIGENVAL'}:
                fail('请选择 XML 路线或文本文件路线，不要同时上传多个结果主文件')
            if {'POSCAR', 'CONTCAR'} <= names:
                fail('请选择与输出相匹配的一份结构文件，避免 POSCAR／CONTCAR 歧义')
            doc.update(status='processing', error=None)
            self.save(doc)
            self.active = ident
            self.thread = threading.Thread(target=self._run, args=(ident,), daemon=True)
            self.thread.start()
            return doc

    def _run(self, ident):
        try:
            with self.guard:
                doc = self.read(ident)
                if doc['status'] != 'processing':
                    return
                directory = self.directory(ident)
                self.verify_sources(doc)
                target = directory / 'worker-result.json'
                target.unlink(missing_ok=True)
                self.process = subprocess.Popen(
                    [sys.executable, '-B', '-X', 'utf8', '-m', 'backend.toolbox.postprocessing.worker', str(directory), doc['kind'], str(target)],
                    cwd=Path(__file__).resolve().parents[3], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                process = self.process
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                fail('解析超过 90 秒，请缩小输入规模', 'PP_TIMEOUT', 408)
            with self.guard:
                doc = self.read(ident)
                if doc['status'] != 'processing':
                    return
                if process.returncode != 0 or not target.exists():
                    fail('独立解析进程未完成，请检查环境与文件', 'PP_WORKER_FAILED', 500)
                response = json.loads(target.read_text(encoding='utf-8'))
                if 'error' in response:
                    doc.update(status='failed', error=response['error'])
                else:
                    result = response['result']
                    atomic(directory / 'result.json', result)
                    doc.update(status='ready', summary={k: v for k, v in result.items() if k != 'data'})
                    doc['summary']['atoms'] = sorted({p['atom']: p['element'] for p in result['data'].get('projected', [])}.items())
                    doc['summary']['orbitals'] = sorted({p['orbital'] for p in result['data'].get('projected', [])})
                    doc['summary']['band_count'] = result['data'].get('band_count', 0)
                    if doc['kind'] == 'band':
                        doc['view']['band_end'] = min(20, doc['summary']['band_count'])
                    doc['view'] = upgrade_view(result, doc['view'], new_analysis=not doc['view'].get('axes'))
                self.save(doc)
        except Exception as exc:
            with self.guard:
                doc = self.read(ident)
                if doc['status'] == 'processing':
                    doc.update(status='failed', error=exc.payload() if isinstance(exc, ToolboxError) else {'code': 'PP_STORAGE_FAILED', 'message': '分析无法保存，请检查本地空间后重试'})
                    self.save(doc)
        finally:
            with self.guard:
                self.active, self.process = None, None

    def cancel(self, ident):
        with self.guard:
            doc = self.read(ident)
            if doc['status'] == 'downloading' and self.task_sources:
                return self.task_sources.cancel(ident)
            if doc['status'] == 'processing':
                if self.active == ident and self.process and self.process.poll() is None:
                    self.process.terminate()
                doc['status'] = 'cancelled'
                self.save(doc)
            return doc

    def close(self):
        if self.task_sources:
            self.task_sources.close()
        if self.active:
            self.cancel(self.active)
        if self.thread:
            self.thread.join(timeout=5)

    def delete(self, ident):
        with self.guard:
            self.read(ident)
            if self.active == ident or ident in self.uploads or (self.task_sources and ident in self.task_sources.active):
                fail('请先取消解析／等待上传完成再删除', 'PP_BUSY', 409)
            # Only this validated UUID child of our snapshot root can be removed.
            directory = self.directory(ident).resolve()
            if directory.parent != self.root.resolve():
                fail('快照路径无效')
            shutil.rmtree(directory)

    @staticmethod
    def file_fingerprint(path):
        if path.is_symlink() or not path.is_file():
            fail('缓存原始文件不存在或不是普通文件', 'PP_SOURCE_CHANGED', 409)
        sha, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            while chunk := stream.read(65536):
                size += len(chunk)
                if size > MAX_FILE:
                    fail('缓存文件超过单文件上限', 'PP_SOURCE_CHANGED', 409)
                sha.update(chunk)
        return size, sha.hexdigest()

    def verify_sources(self, doc):
        directory = self.directory(doc['id'])
        if not doc['files']:
            fail('源快照缺少文件', 'PP_SOURCE_CHANGED', 409)
        for file in doc['files']:
            if file['name'] not in NAMES or self.file_fingerprint(directory / file['name']) != (file['size_bytes'], file['sha256']):
                fail('源快照已变化，请重新导入', 'PP_SOURCE_CHANGED', 409)
        if doc['source']['kind'] == 'task_result':
            recipe = json.loads((directory / 'task-source.json').read_text(encoding='utf-8'))
            if set(recipe['selected_files']) != {f['name'] for f in doc['files']}:
                fail('任务缓存文件集不完整', 'PP_CACHE_INCOMPLETE', 409)

    def view(self, ident, revision, view):
        with self.guard:
            doc = self.read(ident)
            if doc['status'] != 'ready' or doc['revision'] != revision:
                fail('分析版本已变化，请刷新后再保存', 'PP_CONFLICT', 409)
            doc['view'] = self.validate_view(doc, view)
            self.save(doc)
            return doc

    def validate_view(self, doc, view):
        expected = {'reference', 'reference_ev', 'mirror_down', 'atoms', 'orbitals', 'band_start', 'band_end'}
        additions = {'axes', 'version', 'energy_min_ev', 'energy_max_ev', 'elements', 'projection_grouping'}
        if not isinstance(view, dict) or not expected <= set(view) or set(view) - expected - additions or view['reference'] not in ('raw', 'fermi', 'custom', 'legacy_absolute'):
            fail('视图配置不完整')
        v2 = view.get('version') == VIEW_VERSION
        if 'version' in view and not v2:
            fail('视图版本不受支持')
        if v2 and not {'energy_min_ev', 'energy_max_ev', 'elements', 'projection_grouping'} <= set(view):
            fail('能量窗或投影分组配置不完整')
        if not v2 and set(view) & {'energy_min_ev', 'energy_max_ev', 'elements', 'projection_grouping'}:
            fail('新版能量窗与投影字段必须提供视图版本')
        if (view['reference'] == 'fermi' or v2 and view['reference'] == 'custom') and doc['summary']['efermi_ev'] is None:
            fail('数据缺少可信费米能级；请选择原始能量，相对费米偏移不可用', 'PP_MISSING_FERMI')
        if type(view['reference_ev']) not in (int, float) or not math.isfinite(view['reference_ev']):
            fail('参考能量必须为有限数值')
        if type(view['mirror_down']) is not bool:
            fail('自旋显示选项无效')
        atoms = {a[0] for a in doc['summary']['atoms']}
        if not isinstance(view['atoms'], list) or any(type(a) is not int or a not in atoms for a in view['atoms']):
            fail('原子选择无效')
        orbitals = set(doc['summary']['orbitals'])
        if v2:
            orbitals |= {o[:1] for o in orbitals if o[:1] in ('s', 'p', 'd', 'f')}
        if not isinstance(view['orbitals'], list) or any(o not in orbitals for o in view['orbitals']):
            fail('轨道选择无效')
        if any(type(view[k]) is not int for k in ('band_start', 'band_end')) or not 1 <= view['band_start'] <= view['band_end'] <= (doc['summary']['band_count'] if doc['kind'] == 'band' else 20):
            fail('能带范围无效')
        if view.get('axes') is not None:
            validate_axes(view['axes'])
        result = json.loads((self.directory(doc['id']) / 'result.json').read_text(encoding='utf-8'))
        if v2:
            elements = {a[1] for a in doc['summary']['atoms']}
            if not isinstance(view['elements'], list) or any(e not in elements for e in view['elements']):
                fail('元素选择无效')
            if view['projection_grouping'] not in ('element', 'combined'):
                fail('投影分组无效')
            if view['reference'] == 'legacy_absolute' and (doc['view']['reference'] != 'legacy_absolute' or view['reference_ev'] != doc['view']['reference_ev']):
                fail('旧版绝对参考仅用于保留已有图形，请选择原始能量或可信费米参考')
            validate_energy_window(view)
            normalized = dict(view)
        else:
            if view['reference'] == 'legacy_absolute':
                fail('旧版请求不能新建绝对参考兼容模式')
            # Omitted legacy axes retain the previously saved energy window.
            normalized = upgrade_view(result, {**view, 'axes': view.get('axes') or doc['view']['axes']})
        normalized.update(atoms=sorted(set(normalized['atoms'])), orbitals=sorted(set(normalized['orbitals'])),
                          elements=sorted(set(normalized['elements'])))
        normalized['axes'] = fit_axes(result, normalized)
        return normalized

    def fit(self, ident, revision, view):
        with self.guard:
            doc = self.read(ident)
            if doc['status'] != 'ready' or doc['revision'] != revision:
                fail('分析版本已变化，请刷新后再适配', 'PP_CONFLICT', 409)
            view = self.validate_view(doc, view)
            result = json.loads((self.directory(ident) / 'result.json').read_text(encoding='utf-8'))
            bounds = energy_bounds(result, view)
            full_view = {**view, 'energy_min_ev': bounds['min_ev'], 'energy_max_ev': bounds['max_ev']}
            return {'axes': fit_axes(result, full_view), 'energy_bounds_ev': bounds, 'revision': doc['revision']}

    def curves(self, ident):
        with self.guard:
            doc = self.read(ident)
            if doc['status'] != 'ready':
                fail('分析尚未完成', 'PP_NOT_READY', 409)
            result = json.loads((self.directory(ident) / 'result.json').read_text(encoding='utf-8'))
        data, view = result['data'], doc['view']
        curves, reference = prepare_curves(result, view)
        if sum(len(c['x']) * 2 for c in curves) > MAX_RESPONSE_VALUES:
            fail('当前曲线超过显示规模，请减少投影元素或缩小能带范围；未截断原始数据', 'PP_PLOT_TOO_LARGE', 413)
        ticks = []
        for segment in data.get('segments', []):
            for index, label in zip((segment['start'], segment['end']), segment['labels']):
                if label:
                    position = data['distance_inv_angstrom'][index]
                    previous = next((t for t in ticks if abs(t['x'] - position) < 1e-8), None)
                    if previous:
                        if label not in previous['label'].split('|'):
                            previous['label'] += '|' + label
                    else:
                        ticks.append({'x': position, 'label': label})
        return {'id': ident, 'revision': doc['revision'], 'kind': doc['kind'], 'view': view,
                'reference_ev': reference, 'effective_reference_ev': reference,
                'energy_bounds_ev': energy_bounds(result, view), 'curves': curves, 'ticks': ticks,
                'units': {'energy': 'eV', 'dos_total': data.get('density_unit', 'states/eV/cell'),
                          'dos_projection': 'states/eV', 'kpath': 'Å⁻¹', 'reciprocal_convention': '2pi'},
                'sources': doc['files'], 'source': doc['source'], 'parser': result['parser'], 'warnings': result['warnings'],
                'plot_warnings': bounds_warning(curves, doc['kind'], view)}

    def csv(self, ident):
        result = self.curves(ident)
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer)
        writer.writerow(['# VASP-Copilot pp.v1', ident, f"revision={result['revision']}", f"view={result['view']['version']}",
                         f"reference={result['view']['reference']}", f"reference_eV={result['reference_ev']}",
                         f"offset_eV={result['view']['reference_ev']}" if result['view']['reference'] == 'custom' else ''])
        writer.writerow(['# units', 'energy=eV', 'total_DOS=states/eV/cell', 'projection_DOS=states/eV (selected atom sum)', 'kpath=1/angstrom (2pi reciprocal lattice)'])
        writer.writerow(['# provenance', json.dumps(result['source'], ensure_ascii=False, sort_keys=True)])
        for source in result['sources']:
            writer.writerow(['# source', source['name'], source['sha256'], source.get('remote_path', '')])
        writer.writerow(['curve', 'channel', 'segment_index', 'energy_eV' if result['kind'] == 'dos' else 'distance_1/angstrom', 'DOS_states/eV' if result['kind'] == 'dos' else 'energy_eV'])
        for curve in result['curves']:
            for x, y in zip(curve['x'], curve['y']):
                writer.writerow([curve['name'], curve['channel'], curve.get('segment_index', ''), x, y])
        return buffer.getvalue()
