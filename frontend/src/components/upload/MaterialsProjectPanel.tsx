import React, { useRef, useState } from 'react';
import { Alert, Button, Card, Input, Radio, Space, Table, Tag, Typography } from 'antd';
import { DownloadOutlined, GlobalOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useMaterialsImport, useMaterialsInterpret, useMaterialsSearch } from '../../hooks/useApi';
import ErrorAlert from '../common/ErrorAlert';
import { formatMaterialId } from '../../utils/materialId';
import type { MaterialCandidate, StructureSummary } from '../../types/generated-api';
import type { MaterialCriteria, MaterialInterpretation, MaterialSearchResponse } from '../../types/materials';

const { Text } = Typography;
type SearchMode = 'formula' | 'natural';

interface MaterialsProjectPanelProps {
  onStructureImported: (fileId: string, summary: StructureSummary) => void;
}

/** These labels describe Toolbox criteria, never a model's prose. */
function criteriaLabels(criteria: MaterialCriteria | null | undefined): string[] {
  if (!criteria) return [];
  const labels: string[] = [];
  if (criteria.formula) labels.push(`化学式：${criteria.formula}`);
  if (criteria.elements?.length) labels.push(`至少包含元素：${criteria.elements.join('、')}`);
  if (criteria.chemsys) labels.push(`限定化学体系：${criteria.chemsys}`);
  if (criteria.band_gap) {
    const { min, max } = criteria.band_gap;
    if (min !== undefined && max !== undefined) labels.push(`带隙：${min}–${max} eV`);
    else if (min !== undefined) labels.push(`带隙：≥ ${min} eV`);
    else if (max !== undefined) labels.push(`带隙：≤ ${max} eV`);
  }
  if (criteria.is_stable !== undefined) labels.push(`MP 计算稳定性：${criteria.is_stable ? '稳定' : '非稳定'}`);
  if (criteria.is_metal !== undefined) labels.push(`金属性：${criteria.is_metal ? '金属' : '非金属'}`);
  return labels;
}

const MaterialsProjectPanel: React.FC<MaterialsProjectPanelProps> = ({ onStructureImported }) => {
  const searchMutation = useMaterialsSearch();
  const interpretMutation = useMaterialsInterpret();
  const importMutation = useMaterialsImport();
  const revision = useRef(0);
  const [mode, setMode] = useState<SearchMode>('formula');
  const [query, setQuery] = useState('');
  const [interpretation, setInterpretation] = useState<MaterialInterpretation | null>(null);
  const [result, setResult] = useState<MaterialSearchResponse | null>(null);
  const [selected, setSelected] = useState<MaterialCandidate | null>(null);
  const [busy, setBusy] = useState<'interpret' | 'search' | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [done, setDone] = useState(false);

  const invalidate = () => {
    revision.current += 1;
    setInterpretation(null);
    setResult(null);
    setSelected(null);
    setDone(false);
    setBusy(null);
    setError(null);
    searchMutation.reset();
    interpretMutation.reset();
    importMutation.reset();
  };

  const changeMode = (next: SearchMode) => {
    if (next === mode) return;
    invalidate();
    setMode(next);
    setQuery('');
  };

  const changeQuery = (next: string) => {
    invalidate();
    setQuery(next);
  };

  const handleInterpret = async () => {
    const q = query.trim();
    if (!q || busy) return;
    invalidate();
    const ticket = revision.current;
    setBusy('interpret');
    try {
      const parsed = await interpretMutation.mutateAsync(q);
      if (ticket !== revision.current) return;
      if (parsed.query !== q || !Array.isArray(parsed.unresolved_conditions)
          || !Array.isArray(parsed.interpreted_conditions) || !Array.isArray(parsed.warnings)
          || !['ready_for_confirmation', 'needs_clarification'].includes(parsed.status)) {
        throw new Error('解析结果格式不正确，请重新解析或改用明确化学式搜索。');
      }
      setInterpretation(parsed);
    } catch (cause) {
      if (ticket === revision.current) setError(cause instanceof Error ? cause : new Error('材料条件解析失败'));
    } finally {
      if (ticket === revision.current) setBusy(null);
    }
  };

  const canConfirm = mode === 'natural' && interpretation?.status === 'ready_for_confirmation'
    && criteriaLabels(interpretation.criteria).length > 0 && interpretation.unresolved_conditions.length === 0;

  const handleSearch = async () => {
    const q = query.trim();
    if (!q || busy || (mode === 'natural' && !canConfirm)) return;
    const ticket = ++revision.current;
    setResult(null);
    setSelected(null);
    setDone(false);
    setError(null);
    setBusy('search');
    try {
      const response = await searchMutation.mutateAsync(mode === 'formula'
        ? { query: q, limit: 20 }
        : { query: q, criteria: interpretation!.criteria!, confirmed: true, unresolved_conditions: [], limit: 20 });
      if (ticket === revision.current) setResult(response);
    } catch (cause) {
      if (ticket === revision.current) setError(cause instanceof Error ? cause : new Error('材料搜索失败'));
    } finally {
      if (ticket === revision.current) setBusy(null);
    }
  };

  const handleImport = async () => {
    if (!selected || importMutation.isPending) return;
    const ticket = revision.current;
    try {
      const response = await importMutation.mutateAsync(selected.material_id);
      if (ticket !== revision.current) return;
      setDone(true);
      onStructureImported(response.structure_id, response.summary as StructureSummary);
    } catch {
      // The import mutation supplies ErrorAlert below.
    }
  };

  const columns: ColumnsType<MaterialCandidate> = [
    { title: '', key: 'select', width: 48, render: (_, rec) => (
      <Radio checked={selected?.material_id === rec.material_id} onChange={() => setSelected(rec)} />
    ) },
    { title: '化学式', dataIndex: 'formula', width: 120, render: (value: string) => <Text strong>{value || '—'}</Text> },
    { title: '材料ID', dataIndex: 'material_id', width: 220, render: (value: string) => {
      const displayValue = formatMaterialId(value);
      return <Space direction="vertical" size={0}>
        <Text code>{displayValue}</Text>
        {displayValue !== value && <Text type="secondary" style={{ fontSize: 11 }}>
          API ID: <Text type="secondary" copyable={{ text: value }}>{value}</Text>
        </Text>}
      </Space>;
    } },
    { title: '元素', dataIndex: 'elements', width: 200, render: (value: string[]) => (
      <Space size={4} wrap>{(value ?? []).map((el) => <Tag key={el}>{el}</Tag>)}</Space>
    ) },
    { title: '带隙 (eV)', dataIndex: 'band_gap', width: 90, render: (value: number) => value == null ? '—' : value.toFixed(2) },
    { title: '形成能 (eV/atom)', dataIndex: 'formation_energy_per_atom', width: 120,
      render: (value: number) => value == null ? '—' : value.toFixed(3) },
    { title: '空间群', dataIndex: 'spacegroup', width: 150,
      render: (value) => value?.symbol ? `${value.symbol} (#${value.number ?? '?'})` : '—' },
  ];

  return (
    <Card title={<span><GlobalOutlined style={{ color: '#0071e3' }} /> Materials Project 导入结构（可选）</span>} bordered={false}>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Radio.Group aria-label="材料搜索模式" value={mode} onChange={(event) => changeMode(event.target.value as SearchMode)}>
          <Radio.Button value="formula">明确化学式</Radio.Button>
          <Radio.Button value="natural">自然语言条件（AI）</Radio.Button>
        </Radio.Group>
        <Input.TextArea
          aria-label={mode === 'formula' ? '化学式' : '自然语言材料条件'}
          value={query}
          onChange={(event) => changeQuery(event.target.value)}
          placeholder={mode === 'formula' ? '输入明确化学式，例如 Fe2O3 或 NaCl' : '描述材料条件，例如：带隙 1–3 eV、MP 计算稳定、至少含 Fe 和 O'}
          autoSize={{ minRows: 2, maxRows: 4 }}
        />
        <Space wrap>
          {mode === 'natural' && (
            <Button icon={<SearchOutlined />} loading={busy === 'interpret'} disabled={!query.trim() || busy !== null}
              onClick={handleInterpret}>解析条件</Button>
          )}
          <Button type="primary" icon={<SearchOutlined />} loading={busy === 'search'}
            disabled={!query.trim() || busy !== null || (mode === 'natural' && !canConfirm)} onClick={handleSearch}>
            {mode === 'natural' ? '确认条件并搜索' : '搜索化学式'}
          </Button>
          <Button icon={<DownloadOutlined />} disabled={!selected || importMutation.isPending} loading={importMutation.isPending}
            onClick={handleImport}>导入所选结构</Button>
        </Space>

        {mode === 'natural' && interpretation && (
          <Card size="small" title="请核对解析结果">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text>原始需求：{interpretation.query}</Text>
              {interpretation.interpreted_conditions.length > 0 && (
                <div>模型理解：{interpretation.interpreted_conditions.join('；')}</div>
              )}
              <div>将用于搜索的结构化条件：
                {criteriaLabels(interpretation.criteria).length > 0
                  ? criteriaLabels(interpretation.criteria).map((label) => <Tag key={label}>{label}</Tag>)
                  : <Text type="secondary">无可执行条件</Text>}
              </div>
              {interpretation.unresolved_conditions.length > 0 && (
                <Alert type="warning" showIcon message="有未解决条件，当前不能搜索"
                  description={interpretation.unresolved_conditions.join('；')} />
              )}
              {interpretation.warnings.map((warning, index) => <Alert key={index} type="warning" showIcon message={warning} />)}
              {canConfirm && <Text type="secondary">模型解析可能遗漏需求，请对照原始需求核对上方实际条件后再确认。</Text>}
            </Space>
          </Card>
        )}

        {(error || importMutation.error) && (
          <ErrorAlert error={error || importMutation.error!} title={mode === 'natural' ? '操作失败，可重试或改用明确化学式' : '操作失败'}
            onRetry={() => { setError(null); importMutation.reset(); }} />
        )}
        {done && <Alert type="success" showIcon message="材料已导入，可进入下一步流程" />}
        {result && (
          <Card size="small" title={`搜索结果：${result.count} 条候选`}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <div>本次实际应用条件：
                {criteriaLabels(result.criteria).map((label) => <Tag key={label}>{label}</Tag>)}
              </div>
              <Text type="secondary">MP 稳定性是计算数据中的标志，不能证明实验可合成。</Text>
              {result.materials.length === 0 && <Alert type="info" showIcon message="未找到匹配材料，请调整条件后重试" />}
            </Space>
          </Card>
        )}
        {result && result.materials.length > 0 && (
          <Table<MaterialCandidate> rowKey="material_id" columns={columns} dataSource={result.materials}
            size="small" pagination={{ pageSize: 5 }} scroll={{ x: 1000 }} />
        )}
      </Space>
    </Card>
  );
};

export default MaterialsProjectPanel;
