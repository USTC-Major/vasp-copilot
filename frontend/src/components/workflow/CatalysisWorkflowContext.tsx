import { Card, Descriptions, Typography } from 'antd';
import { Link } from 'react-router-dom';
import type { CatalysisSurfacePolicy, CatalysisWorkflowBinding } from '../../types/catalysis';

export default function CatalysisWorkflowContext({ binding, policy }: { binding: CatalysisWorkflowBinding; policy: CatalysisSurfacePolicy }) {
  const fixed = binding.snapshot.atoms.filter(atom => atom.selective_dynamics.every(flag => !flag)).length;
  const warnings = [...new Set([...binding.warnings, ...policy.warnings])];
  return <Card size="small" title="表面计算场景 · CAT 来源与约束" style={{ marginBottom: 16 }}>
    <Descriptions column={1} size="small">
      <Descriptions.Item label="模型">{binding.model_kind === 'clean_surface' ? '清洁表面' : '单个吸附候选'} · {binding.snapshot.atoms.length} 原子 · {fixed} 个全固定</Descriptions.Item>
      <Descriptions.Item label="晶胞弛豫">固定晶胞，relax 使用 ISIF = {policy.relax_isif}；保留逐原子约束。</Descriptions.Item>
      <Descriptions.Item label="k 点采样">{policy.kpoint_mode === 'explicit_gamma_2d' ? '显式 Gamma 二维面内网格（倾斜 c 晶胞）' : 'Gamma 二维网格 Nx × Ny × 1'}；法向采样数 {policy.normal_sampling_count}。</Descriptions.Item>
    </Descriptions>
    <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>请核对几何适用性、磁性、电荷与偶极修正{policy.kpoint_mode === 'explicit_gamma_2d' ? '；倾斜 c 的 DOS 需确认占据法。' : '。'}</Typography.Paragraph>
    <details>
      <summary style={{ cursor: 'pointer' }}>来源与约束详情{warnings.length > 0 ? `（${warnings.length} 条说明）` : ''}</summary>
      <div style={{ marginTop: 12, overflowWrap: 'anywhere' }}>
        <Descriptions column={1} size="small" styles={{ content: { overflowWrap: 'anywhere' } }}>
          <Descriptions.Item label="来源">{binding.source.name || binding.source.kind}</Descriptions.Item>
          <Descriptions.Item label="CAT 草稿">{binding.draft_id} · revision {binding.revision}</Descriptions.Item>
          <Descriptions.Item label="表面 / 候选">{binding.surface_id}{binding.candidate_id ? ` / ${binding.candidate_id}` : ''}</Descriptions.Item>
          <Descriptions.Item label="清洁表面快照">{binding.parent_clean_snapshot_id}</Descriptions.Item>
          <Descriptions.Item label="已绑定快照">{binding.snapshot_id}</Descriptions.Item>
          <Descriptions.Item label="约束基准">逐原子 T/F 沿直接晶格 a/b/c 保留。</Descriptions.Item>
        </Descriptions>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>已绑定不可变快照；之后的 CAT 编辑需再次确认传入才会替换本草稿。</Typography.Paragraph>
        {policy.kpoint_mode === 'explicit_gamma_2d' && <Typography.Paragraph type="secondary">此二维网格不支持四面体占据法；已有 DOS 默认使用 ISMEAR = -5。选择 DOS 时需在确认参数前明确选择占据设置，后续按已有补丁机制传递并审阅。</Typography.Paragraph>}
        {warnings.length > 0 && <ul style={{ margin: '8px 0', paddingLeft: 20 }}>{warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>}
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>策略依据：{policy.sources.map((url, index) => <span key={url}>{index > 0 && ' · '}<a href={url} target="_blank" rel="noreferrer">{url.endsWith('ISIF') ? 'VASP ISIF' : url.endsWith('KPOINTS') ? 'VASP KPOINTS' : 'VASP 表面与二维材料'}</a></span>)}</Typography.Paragraph>
        <Link to={`/toolbox/surface-builder?draft=${encodeURIComponent(binding.draft_id)}`}>查看来源建模草稿</Link>
      </div>
    </details>
  </Card>;
}
