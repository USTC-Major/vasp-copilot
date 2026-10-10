"""Durable revisioned CAT drafts in the Toolbox's independent namespace."""
from __future__ import annotations

import copy
import datetime
import io
import json
import re
import threading
import uuid
import zipfile
from pathlib import Path

import numpy as np
from numpy.linalg import LinAlgError
from pymatgen.core import Lattice

from ..contracts import ToolboxError
from . import science, adsorption
from .schemas import SurfaceParams

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
MAX_DRAFTS = 128
MAX_REVISIONS = 256
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_STORE_BYTES = 256 * 1024 * 1024


class CatalysisError(ToolboxError):
    def __init__(self, code, message, status=422, *, details=None):
        super().__init__(code, message, status)
        self.details = details

    def payload(self):
        result = super().payload()
        if self.details:
            result['details'] = self.details
        return result


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def clean_name(value):
    value = value.strip()
    if not value or any(ord(c) < 32 for c in value):
        raise CatalysisError('CAT_NAME_INVALID', '名称须非空且不含控制字符')
    return value


class CatalysisService:
    def __init__(self, root: Path):
        self.root = Path(root) / 'catalysis'
        self.directory = self.root / 'drafts'
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.root.resolve()), threading.RLock())

    def _folder(self, draft_id):
        if not re.fullmatch(r'cat_[0-9a-f]{32}', draft_id):
            raise CatalysisError('CAT_DRAFT_NOT_FOUND', '草稿不存在', 404)
        return self.directory / draft_id

    def _read(self, draft_id):
        folder = self._folder(draft_id)
        try:
            head = json.loads((folder / 'head.json').read_text(encoding='utf-8'))
            revision = head['revision']
            if not isinstance(revision, int) or not 1 <= revision <= MAX_REVISIONS:
                raise ValueError('revision')
            filename = head['file']
            if not re.fullmatch(r'revision-[0-9]{6}-[0-9a-f]{32}\.json', filename):
                raise ValueError('revision file')
            raw = (folder / filename).read_bytes()
            if len(raw) > MAX_DOCUMENT_BYTES or science.sha(raw) != head['sha256']:
                raise ValueError('hash')
            draft = json.loads(raw)
            if draft['draft_id'] != draft_id or draft['revision'] != revision or draft['schema_version'] != 1:
                raise ValueError('identity')
            for snap in [draft['input_snapshot'], *(s['snapshot'] for s in draft['surfaces']),
                         *(c['snapshot'] for c in (draft.get('adsorption') or {}).get('candidates', []))]:
                payload = {k: v for k, v in snap.items() if k != 'sha256'}
                if science.sha(science.canonical(payload)) != snap['sha256']:
                    raise ValueError('snapshot hash')
                ids = [a['atom_id'] for a in snap['atoms']]
                if len(set(ids)) != len(ids):
                    raise ValueError('snapshot atom identities')
            return draft
        except FileNotFoundError as exc:
            raise CatalysisError('CAT_DRAFT_NOT_FOUND', '草稿不存在或存储记录缺失', 404) from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CatalysisError('CAT_DRAFT_STORE_INVALID', '草稿存储校验失败；原文件保留', 503) from exc

    def get(self, draft_id):
        with self.lock:
            return self._read(draft_id)

    def _check_revision(self, draft, revision):
        if revision != draft['revision']:
            raise CatalysisError('CAT_REVISION_CONFLICT', '草稿已更新，请重新载入后操作', 409,
                                 details={'current_revision': draft['revision']})

    def _write(self, draft, *, source_text=None):
        folder = self._folder(draft['draft_id'])
        raw = science.canonical(draft)
        if len(raw) > MAX_DOCUMENT_BYTES or draft['revision'] > MAX_REVISIONS:
            raise CatalysisError('CAT_DRAFT_RESOURCE_LIMIT', '草稿超过 8 MiB 或 256 次修订上限')
        used = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file()) if self.root.exists() else 0
        if used + len(raw) + (len(source_text.encode('utf-8')) if source_text else 0) > MAX_STORE_BYTES:
            raise CatalysisError('CAT_STORE_RESOURCE_LIMIT', '催化草稿总存储超过 256 MiB 上限')
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if source_text is not None:
                with (folder / 'source.txt').open('x', encoding='utf-8', newline='') as handle:
                    handle.write(source_text)
            # Unchanged revisions remain immutable. Publish head only after the
            # entire next revision is on disk. Failure leaves the prior head.
            revision_path = folder / f"revision-{draft['revision']:06d}-{uuid.uuid4().hex}.json"
            with revision_path.open('xb') as handle:
                handle.write(raw)
                handle.flush()
            head = folder / ('head-' + uuid.uuid4().hex + '.tmp')
            head.write_bytes(science.canonical({'revision': draft['revision'], 'sha256': science.sha(raw), 'file': revision_path.name}))
            head.replace(folder / 'head.json')
        except OSError as exc:
            raise CatalysisError('CAT_DRAFT_WRITE_FAILED', '草稿无法持久化，未发布新修订；请检查磁盘权限或空间', 503) from exc
        return draft

    def _next(self, draft):
        draft['revision'] += 1
        draft['updated_at'] = now()
        return self._write(draft)

    def list(self):
        with self.lock:
            rows = []
            if not self.directory.exists():
                return rows
            for path in self.directory.glob('cat_*'):
                if not path.is_dir():
                    continue
                draft = self._read(path.name)
                selected = next((s for s in draft['surfaces'] if s['surface_id'] == draft['active_surface_id']), None)
                rows.append({'draft_id': draft['draft_id'], 'revision': draft['revision'], 'name': draft['name'],
                             'updated_at': draft['updated_at'], 'source_role': draft['source']['role'],
                             'atom_count': len((selected['snapshot'] if selected else draft['input_snapshot'])['atoms']),
                             'surface_count': len(draft['surfaces']), 'active_surface_id': draft['active_surface_id']})
            return sorted(rows, key=lambda row: row['updated_at'], reverse=True)

    def create(self, body, *, resolve_structure=None):
        source = body.source
        maximum = science.MAX_BULK_ATOMS if source.role == 'bulk' else science.MAX_ATOMS
        warnings = ['几何建模未验证表面稳定性、极性、磁性、重构或计算收敛；hkl 相对于提供的晶胞。']
        original_name = source.name or ('ideal-Pt.POSCAR' if source.kind == 'example' else 'POSCAR')
        source_sha = None
        original_format = source.kind
        if source.kind == 'example':
            text = science.ideal_pt()
            warnings.append('示例是 a=3.92 Å 的合成理想 fcc Pt 常规胞，未经实验或 DFT 优化。')
        elif source.kind == 'structure_id':
            if resolve_structure is None:
                raise CatalysisError('CAT_SOURCE_UNAVAILABLE', '当前服务无法读取已有结构来源')
            record = resolve_structure(source.structure_id)
            text = record.summary.poscar_text
            original_name = record.summary.source_file or 'POSCAR'
            original_format = 'materials_project' if record.summary.source_material_id else 'poscar_snapshot'
            source_sha = record.summary.source_sha256
            if source_sha and science.sha(text.encode('utf-8')) != source_sha:
                raise CatalysisError('CAT_SOURCE_HASH_MISMATCH', '已有结构来源正文与指纹不一致')
        else:
            text = source.content
        if len(text.encode('utf-8')) > science.MAX_TEXT_BYTES:
            raise CatalysisError('CAT_INPUT_SIZE_LIMIT', '结构文本超过 2 MiB 上限')
        source_sha = science.sha(text.encode('utf-8'))
        original_text = text
        if source.kind == 'cif':
            text = science.cif_poscar(text, maximum)
            warnings.append('CIF 按文件晶胞展开有序位点，无 primitive 化或标准化；CIF 不提供 VASP 逐原子 T/F。')
        snap = science.import_poscar(text, maximum)
        ident = 'cat_' + uuid.uuid4().hex
        surfaces = []
        if source.role == 'slab':
            surfaces = [{'surface_id': 'surf_' + uuid.uuid4().hex, 'termination_shift': None,
                         'snapshot': copy.deepcopy(snap), 'baseline_flags': [a['selective_dynamics'] for a in snap['atoms']],
                         'surface': science.surface_geometry(snap, .1),
                         'transform': {'version': science.TRANSFORM_VERSION, 'kind': 'imported_slab',
                                       'input_snapshot_id': snap['snapshot_id'], 'cell_convention': 'supplied_input_cell',
                                       'standardize': False, 'primitive': False, 'identity_relation': 'unchanged_imported_atoms'}}]
            warnings.append('已有 slab 按 a×b 法向、最大跨周期无核空隙展开分层；这不自动判定其物理真空或表面适用性。')
        draft = {'draft_id': ident, 'schema_version': 1, 'revision': 1,
                 'name': clean_name(body.name), 'created_at': now(), 'updated_at': now(),
                 'source': {'kind': source.kind, 'role': source.role, 'name': clean_name(original_name),
                            'sha256': source_sha, 'format': original_format, 'structure_id': source.structure_id,
                            'cell_convention': 'supplied_input_cell', 'standardized': False},
                 'input_snapshot': snap, 'parameters': SurfaceParams().model_dump(mode='json') if source.role == 'bulk' else None,
                 'surfaces': surfaces, 'active_surface_id': surfaces[0]['surface_id'] if surfaces else None,
                 'warnings': warnings}
        with self.lock:
            if self.directory.exists() and len(list(self.directory.glob('cat_*'))) >= MAX_DRAFTS:
                raise CatalysisError('CAT_DRAFT_COUNT_LIMIT', '草稿数量达到 128 个上限')
            return self._write(draft, source_text=original_text)

    def patch(self, draft_id, body):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, body.revision)
            fields = body.model_fields_set
            if 'name' in fields:
                if body.name is None:
                    raise CatalysisError('CAT_NAME_INVALID', '名称不能为空')
                draft['name'] = clean_name(body.name)
            if 'parameters' in fields:
                if draft['source']['role'] != 'bulk':
                    raise CatalysisError('CAT_SOURCE_ROLE_INVALID', '已有 slab 不进行切面参数修改')
                parameters = body.parameters.model_dump(mode='json') if body.parameters else None
                if parameters != draft['parameters']:
                    self._invalidate_adsorption(draft, '表面生成参数已改变，请重新生成位点和候选')
                    draft['parameters'] = parameters
                    draft['surfaces'] = []
                    draft['active_surface_id'] = None
            if 'active_surface_id' in fields:
                if not any(s['surface_id'] == body.active_surface_id for s in draft['surfaces']):
                    raise CatalysisError('CAT_SURFACE_NOT_FOUND', '终止面已过期或不存在', 409)
                if draft['active_surface_id'] != body.active_surface_id:
                    self._invalidate_adsorption(draft, '选中终止面已改变，请重新生成位点和候选')
                    draft['active_surface_id'] = body.active_surface_id
            return self._next(draft)

    def build(self, draft_id, body):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, body.revision)
            if draft['source']['role'] != 'bulk':
                raise CatalysisError('CAT_SOURCE_ROLE_INVALID', '已有 slab 跳过切面；若需重切请作为 bulk 重新导入')
            params = SurfaceParams.model_validate(body.model_dump(exclude={'revision'}))
            try:
                surfaces = science.generate_surfaces(draft['input_snapshot'], params)
            except ToolboxError:
                raise
            except (ValueError, RuntimeError, TypeError, LinAlgError) as exc:
                raise CatalysisError('CAT_SURFACE_GENERATION_FAILED', '当前输入无法可靠生成表面；原草稿保留') from exc
            draft['parameters'] = params.model_dump(mode='json')
            self._invalidate_adsorption(draft, '表面快照已重新生成，请重新生成位点和候选')
            draft['surfaces'] = surfaces
            draft['active_surface_id'] = surfaces[0]['surface_id']
            return self._next(draft)

    def selected(self, draft, surface_id=None):
        ident = surface_id or draft['active_surface_id']
        selected = next((s for s in draft['surfaces'] if s['surface_id'] == ident), None)
        if selected is None:
            raise CatalysisError('CAT_SURFACE_NOT_FOUND', '请先生成并选择表面，旧终止面不可继续使用', 409)
        return selected

    def bind_workflow(self, draft_id, body, *, file_store):
        """Copy exactly one current model into the existing persistent FileStore.

        The CAT draft is unchanged. Future edits do not mutate this independent
        structure; another explicit handoff creates another structure identity.
        """
        from backend.input_validation import InputValidationError, validate_poscar
        from backend.app.schemas.structure import build_structure_summary, to_structure_context
        from backend.app.schemas.surface import CatalysisBinding
        from backend.app.services.surface_inputs import surface_policy, validate_surface_context
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, body.revision)
            parent = self.selected(draft)
            candidate = None
            if body.candidate_id:
                candidate = self._candidate(draft, body.candidate_id)
                if not candidate['validation']['screening_passed']:
                    raise CatalysisError('CAT_CANDIDATE_INVALID', '候选未通过已保存的距离筛查，请调整并重新生成', 409)
                snap = candidate['snapshot']
            else:
                if body.surface_id != parent['surface_id']:
                    raise CatalysisError('CAT_SURFACE_STALE', '须传入当前已选择的清洁表面，请先应用终止面选择', 409)
                snap = parent['snapshot']
            text, rows = science.poscar_export(snap)
            binding = CatalysisBinding(
                binding_id='catbind_' + uuid.uuid4().hex, draft_id=draft_id, revision=body.revision,
                model_kind='adsorption_candidate' if candidate else 'clean_surface',
                surface_id=parent['surface_id'], candidate_id=body.candidate_id,
                snapshot_id=snap['snapshot_id'], snapshot_sha256=snap['sha256'],
                parent_clean_snapshot_id=parent['snapshot']['snapshot_id'],
                parent_clean_snapshot_sha256=parent['snapshot']['sha256'],
                poscar_sha256=science.sha(text.encode()), poscar_row_mapping=rows,
                source=copy.deepcopy(draft['source']), parameters=copy.deepcopy(draft['parameters']),
                surface=copy.deepcopy(parent['surface']), transform=copy.deepcopy(parent['transform']),
                adsorbate=copy.deepcopy(draft['adsorption']['adsorbate']) if candidate else None,
                candidate=copy.deepcopy(candidate), snapshot=copy.deepcopy(snap),
                parent_clean_snapshot=copy.deepcopy(parent['snapshot']),
                warnings=list(draft['warnings']) + (candidate['validation']['warnings'] if candidate else []))
            # Occupied span/gap of an adsorbed model cannot inherit clean values.
            if candidate:
                binding.surface['model_geometry'] = science.surface_geometry(snap, parent['surface']['layer_tolerance'])
            try:
                policy = surface_policy(snap['lattice'], parent['surface']['normal'])
                info = validate_poscar(text)
                summary = build_structure_summary(poscar_text=text, elements=list(info.elements),
                    counts=list(info.counts), source_file='CAT snapshot POSCAR', validated=info)
                summary.catalysis_binding = binding
                summary.surface_policy = policy
                validate_surface_context(to_structure_context(summary))
            except InputValidationError as exc:
                raise CatalysisError(exc.code, str(exc)) from exc
            # No writes occur until all mapping/science checks have succeeded.
            stored = file_store.store_file('POSCAR', 'poscar', text.encode())
            record = file_store.store_structure(stored.file_id, summary)
            return record.summary

    def constraints(self, draft_id, body):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, body.revision)
            selected = self.selected(draft)
            layers = selected['surface']['layers']
            if body.bottom_fixed_layers > len(layers):
                raise CatalysisError('CAT_FIXED_LAYER_INVALID', '固定层数超过识别层数')
            snap = selected['snapshot']
            ids = {a['atom_id'] for a in snap['atoms']}
            if set(body.atom_overrides) - ids:
                raise CatalysisError('CAT_ATOM_ID_INVALID', '逐原子例外包含过期或未知身份', 409)
            fixed = {i for layer in layers[:body.bottom_fixed_layers] for i in layer['atom_ids']}
            structure = science.from_snapshot(snap)
            flags = []
            for a, baseline in zip(snap['atoms'], selected['baseline_flags']):
                row = [True] * 3 if body.reset_existing else list(baseline)
                if a['atom_id'] in fixed:
                    row = [False] * 3
                override = body.atom_overrides.get(a['atom_id'])
                if override is not None:
                    row = [override == 'free'] * 3
                flags.append(row)
            structure.add_site_property('selective_dynamics', flags)
            selected['snapshot'] = science.snapshot(structure, coordinate_mode=snap['coordinate_mode'],
                                                    provenance=[a['provenance'] for a in snap['atoms']],
                                                    atom_ids=[a['atom_id'] for a in snap['atoms']])
            selected['surface'].update(bottom_fixed_layers=body.bottom_fixed_layers,
                                       atom_overrides=body.atom_overrides, reset_existing=body.reset_existing)
            self._invalidate_adsorption(draft, '表面约束快照已改变，请重新生成位点和候选')
            return self._next(draft)

    def geometry(self, draft_id, revision, surface_id=None, candidate_id=None):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, revision)
            if candidate_id:
                if surface_id:
                    raise CatalysisError('CAT_GEOMETRY_SELECTION_INVALID', '预览须选择表面或候选其中一个')
                candidate = self._candidate(draft, candidate_id)
                return {'mode': 'toolbox', 'revision': revision, 'candidate_id': candidate_id,
                        'surface_id': candidate['parent_surface_id'], 'geometry': science.viewer_geometry(candidate['snapshot'])}
            selected = self.selected(draft, surface_id)
            return {'mode': 'toolbox', 'revision': revision, 'surface_id': selected['surface_id'],
                    'geometry': science.viewer_geometry(selected['snapshot'])}

    def export(self, draft_id, revision, surface_id=None):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, revision)
            selected = self.selected(draft, surface_id)
            text, rows = science.poscar_export(selected['snapshot'])
            poscar = text.encode('utf-8')
            metadata = {'schema_version': 1, 'draft_id': draft_id, 'revision': revision,
                        'source': draft['source'], 'parameters': draft['parameters'],
                        'input_snapshot': draft['input_snapshot'],
                        'surface_id': selected['surface_id'], 'snapshot': selected['snapshot'],
                        'surface': selected['surface'], 'transform': selected['transform'],
                        'poscar_sha256': science.sha(poscar), 'poscar_row_mapping': rows,
                        'selective_flags_basis': 'direct_lattice_vectors', 'warnings': draft['warnings'],
                        'identity_restore_rule': 'Only restore atom IDs after verifying POSCAR and metadata hashes; external edits require fresh import.'}
            encoded = science.canonical(metadata)
            manifest = science.canonical({'schema_version': 1, 'files': {
                'POSCAR': {'sha256': science.sha(poscar)}, 'metadata.json': {'sha256': science.sha(encoded)}}})
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr('POSCAR', poscar)
                bundle.writestr('metadata.json', encoded)
                bundle.writestr('manifest.json', manifest)
            return output.getvalue()

    def _adsorption(self, draft):
        # Only opt into new optional fields when an adsorption action occurs.
        # Reading old schema-1 drafts must not reserialize/hash old snapshots.
        if draft.get('adsorption') is None:
            draft['adsorption'] = {'adsorbate': None, 'sites': [], 'selected_site_ids': [],
                                   'candidates': [], 'selected_candidate_ids': [], 'warnings': []}
        return draft['adsorption']

    def _invalidate_adsorption(self, draft, reason):
        state = draft.get('adsorption')
        if not state:
            return
        state.update(sites=[], selected_site_ids=[], selected_candidate_ids=[],
                     site_parent_snapshot_sha256=None, site_parent_surface_id=None, site_parent_revision=None, frame=None,
                     site_surface_atoms=[], site_map_origin_cartesian=None)
        for candidate in state['candidates']:
            candidate.update(status='stale', invalidation_reason=reason)
        state['warnings'] = [reason] if state['candidates'] else []

    def set_adsorbate(self, draft_id, body):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, body.revision)
            self.selected(draft)
            record = adsorption.parse_adsorbate(body.source, body.anchor_index)
            self._invalidate_adsorption(draft, '吸附物来源或锚点已改变，请重新生成位点和候选')
            self._adsorption(draft)['adsorbate'] = record
            return self._next(draft)

    def find_adsorption_sites(self, draft_id, body):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, body.revision)
            surface = self.selected(draft)
            sites, frame = adsorption.find_sites(surface, body)
            self._invalidate_adsorption(draft, '位点设置已重新生成，请重新生成候选')
            state = self._adsorption(draft)
            state.update(sites=sites, frame=frame, site_parent_snapshot_sha256=surface['snapshot']['sha256'],
                         site_parent_surface_id=surface['surface_id'], site_parent_revision=body.revision,
                         site_settings=body.model_dump(mode='json', exclude={'revision'}))
            matrix, _, unwrapped = adsorption.frame_and_unwrapped(surface)
            lattice = Lattice(matrix)
            top_ids = set(surface['surface']['layers'][-1]['atom_ids'])
            state['site_surface_atoms'] = [{'atom_id': atom['atom_id'], 'element': atom['element'],
                'cartesian': adsorption.canonical_ab(point, lattice)[0].tolist()}
                for atom, point in zip(surface['snapshot']['atoms'], unwrapped) if atom['atom_id'] in top_ids]
            top_height = float((unwrapped @ np.array(frame['z'])).max())
            state['site_map_origin_cartesian'] = (matrix[2] * top_height / surface['surface']['normal_period_angstrom']).tolist()
            state['warnings'] = ['自动位点仅按已保存顶层的几何节点/边中点/锐角三角形重心识别；不判定 fcc/hcp、成键、能量或稳定性。']
            return self._next(draft)

    def build_adsorption_candidates(self, draft_id, body):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, body.revision)
            surface = self.selected(draft)
            state = self._adsorption(draft)
            record = state.get('adsorbate')
            if not record:
                raise CatalysisError('CAT_ADSORBATE_REQUIRED', '请先保存单原子或 XYZ 吸附物及锚点')
            if state.get('site_parent_snapshot_sha256') != surface['snapshot']['sha256'] or state.get('site_parent_surface_id') != surface['surface_id']:
                raise CatalysisError('CAT_ADSORPTION_SITES_STALE', '位点不属于当前表面快照，请重新生成', 409)
            by_id = {site['site_id']: site for site in state['sites']}
            if any(ident not in by_id for ident in body.site_ids):
                raise CatalysisError('CAT_ADSORPTION_SITE_NOT_FOUND', '所选位点已过期或不存在，请重新生成并选择', 409)
            total_atoms = (len(surface['snapshot']['atoms']) + len(record['atoms'])) * len(body.site_ids)
            if total_atoms > adsorption.MAX_CANDIDATE_ATOMS:
                raise CatalysisError('CAT_CANDIDATE_BUDGET_LIMIT', f'本批次总原子预算 {total_atoms} 超过 8192；请减少所选位点或缩小表面')
            candidates = []
            for ident in body.site_ids:
                try:
                    candidates.append(adsorption.build_candidate(surface, record, by_id[ident], body.placement, body.revision))
                except ToolboxError as exc:
                    message = f"位点 {by_id[ident]['label']}（{ident}）：{exc}；本批次未发布，原候选保留"
                    raise CatalysisError(exc.code, message, exc.status,
                                         details={'site_id': ident, 'site_label': by_id[ident]['label'],
                                                  'requested_count': len(body.site_ids), 'published_count': 0}) from exc
            # Replace the current batch atomically. Previous immutable revision
            # files keep their snapshots; no unbounded append or silent skips.
            state.update(candidates=candidates, placement=body.placement.model_dump(mode='json'),
                         selected_site_ids=list(body.site_ids), selected_candidate_ids=[c['candidate_id'] for c in candidates])
            state['warnings'] = ['成功生成的每个候选含一个吸附物；距离筛查不是普适成键或稳定性判据。']
            return self._next(draft)

    def _candidate(self, draft, candidate_id):
        state = draft.get('adsorption') or {}
        candidate = next((c for c in state.get('candidates', []) if c['candidate_id'] == candidate_id), None)
        if candidate is None:
            raise CatalysisError('CAT_CANDIDATE_NOT_FOUND', '候选不存在或已被新批次替换', 409)
        surface = next((s for s in draft['surfaces'] if s['surface_id'] == draft['active_surface_id']), None)
        record = state.get('adsorbate')
        if (candidate['status'] != 'valid' or surface is None or record is None or
                candidate['parent_surface_id'] != surface['surface_id'] or
                candidate['parent_snapshot_sha256'] != surface['snapshot']['sha256'] or
                candidate['adsorbate_source_id'] != record['source_id']):
            raise CatalysisError('CAT_CANDIDATE_STALE', '候选的来源表面、约束或吸附物已改变，请重新生成', 409)
        return candidate

    def select_adsorption_candidates(self, draft_id, body):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, body.revision)
            for ident in body.selected_candidate_ids:
                self._candidate(draft, ident)
            self._adsorption(draft)['selected_candidate_ids'] = list(body.selected_candidate_ids)
            return self._next(draft)

    def export_adsorption_candidates(self, draft_id, body):
        with self.lock:
            draft = self._read(draft_id)
            self._check_revision(draft, body.revision)
            candidates = [self._candidate(draft, ident) for ident in body.candidate_ids]
            surface = self.selected(draft)
            output = io.BytesIO()
            files = {}
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
                for candidate in candidates:
                    prefix = candidate['candidate_id'] + '/'
                    text, rows = science.poscar_export(candidate['snapshot'])
                    poscar = text.encode('utf-8')
                    metadata = {'schema_version': 1, 'draft_id': draft_id, 'revision': body.revision,
                                'source': draft['source'], 'parameters': draft['parameters'],
                                'input_snapshot': draft['input_snapshot'], 'parent_surface': surface,
                                'adsorbate': draft['adsorption']['adsorbate'], 'candidate': candidate,
                                'poscar_sha256': science.sha(poscar), 'poscar_row_mapping': rows,
                                'selective_flags_basis': 'direct_lattice_vectors',
                                'warnings': draft['warnings'] + candidate['validation']['warnings'],
                                'identity_restore_rule': 'Only restore IDs after verifying POSCAR and metadata hashes; external edits require fresh import.'}
                    for filename, content in (('POSCAR', poscar), ('metadata.json', science.canonical(metadata))):
                        path = prefix + filename
                        bundle.writestr(path, content)
                        files[path] = {'sha256': science.sha(content)}
                manifest = {'schema_version': 1, 'draft_id': draft_id, 'revision': body.revision,
                            'candidate_ids': list(body.candidate_ids), 'candidate_count': len(candidates),
                            'files': files, 'purpose': 'independent_geometric_candidates; no_energy_ranking_or_job_execution'}
                bundle.writestr('manifest.json', science.canonical(manifest))
            return output.getvalue()
