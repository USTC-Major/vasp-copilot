// ============================================================
// MaterialsProjectPanel — 从 Materials Project 检索并导入结构
// 支持自然语言需求（LLM 解析，可选）与结构化搜索
// ============================================================

import React, { useState, useCallback } from 'react';
import {
  Card, Input, Button, Space, Typography, Table, Tag, Alert, Spin, Radio,
} from 'antd';
import {
  GlobalOutlined, SearchOutlined, DownloadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useMaterialsSearch, useMaterialsImport } from '../../hooks/useApi';
import ErrorAlert from '../common/ErrorAlert';
import type { MaterialCandidate, StructureSummary } from '../../types/generated-api';

const { Text } = Typography;

interface MaterialsProjectPanelProps {
  onStructureImported: (fileId: string, summary: StructureSummary) => void;
}

const MaterialsProjectPanel: React.FC<MaterialsProjectPanelProps> = ({ onStructureImported }) => {
  const searchMutation = useMaterialsSearch();
  const importMutation = useMaterialsImport();
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<MaterialCandidate | null>(null);
  const [done, setDone] = useState(false);

  const handleSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setSelected(null);
    setDone(false);
    await searchMutation.mutateAsync({ query: q, limit: 20 });
  }, [query, searchMutation]);

  const handleImport = useCallback(async () => {
    if (!selected) return;
    try {
      const resp = await importMutation.mutateAsync(selected.material_id);
      setDone(true);
      onStructureImported(resp.structure_id, resp.summary as StructureSummary);
    } catch {
      // handled by ErrorAlert
    }
  }, [selected, importMutation, onStructureImported]);

  const results = searchMutation.data?.materials ?? [];
  const loading = importMutation.isPending;

  const columns: ColumnsType<MaterialCandidate> = [
    {
      title: '',
      key: 'select',
      width: 48,
      render: (_, rec) => (
        <Radio
          checked={selected?.material_id === rec.material_id}
          onChange={() => setSelected(rec)}
        />
      ),
    },
    {
      title: '化学式',
      dataIndex: 'formula',
      width: 120,
      render: (value: string) => <Text strong>{value || '—'}</Text>,
    },
    {
      title: '材料ID',
      dataIndex: 'material_id',
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: '元素',
      dataIndex: 'elements',
      width: 200,
      render: (value: string[]) => (
        <Space size={4} wrap>
          {(value ?? []).map((el) => <Tag key={el}>{el}</Tag>)}
        </Space>
      ),
    },
    {
      title: '带隙 (eV)',
      dataIndex: 'band_gap',
      width: 90,
      render: (value: number) => (value == null ? '—' : value.toFixed(2)),
    },
    {
      title: '形成能 (eV/atom)',
      dataIndex: 'formation_energy_per_atom',
      width: 120,
      render: (value: number) => (value == null ? '—' : value.toFixed(3)),
    },
    {
      title: '空间群',
      dataIndex: 'spacegroup',
      width: 150,
      render: (value) =>
        value?.symbol
          ? `${value.symbol} (#${value.number ?? '?'})`
          : '—',
    },
  ];

  return (
    <Card
      title={<span><GlobalOutlined style={{ color: '#0071e3' }} /> Materials Project 导入结构（可选）</span>}
      bordered={false}
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Input.TextArea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="输入材料描述或需求，例如：Fe2O3 或「带1-3 eV 的稳定氧化物」；接入 LLM 后可直接用自然语言"
          autoSize={{ minRows: 2, maxRows: 4 }}
          style={{ fontSize: 14 }}
        />
        <Space>
          <Button
            type="primary"
            icon={<SearchOutlined />}
            loading={searchMutation.isPending}
            disabled={!query.trim()}
            onClick={handleSearch}
          >
            搜索材料
          </Button>
          <Button
            type="default"
            icon={<DownloadOutlined />}
            disabled={!selected || loading}
            loading={importMutation.isPending}
            onClick={handleImport}
          >
            导入所选结构
          </Button>
          {selected && !done && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              已选择 {selected.formula}（{selected.material_id}）
            </Text>
          )}
        </Space>

        {(searchMutation.error || importMutation.error) && (
          <ErrorAlert
            error={searchMutation.error || importMutation.error!}
            onRetry={() => {
              searchMutation.reset();
              importMutation.reset();
            }}
          />
        )}

        {done && <Alert type="success" showIcon message="材料已导入，可进入下一步流程" />}

        {searchMutation.isPending && (
          <div style={{ textAlign: 'center', padding: 16 }}>
            <Spin tip="检索中…" />
          </div>
        )}
      </Space>

      {searchMutation.data && !searchMutation.isPending && results.length > 0 && (
        <Table<MaterialCandidate>
          rowKey="material_id"
          columns={columns}
          dataSource={results}
          size="small"
          pagination={{ pageSize: 5 }}
          style={{ marginTop: 16 }}
          scroll={{ x: 600 }}
        />
      )}

      {searchMutation.data && !searchMutation.isPending && results.length === 0 && (
        <Alert
          type="info"
          showIcon
          message="未找到匹配材料，请调整描述或检查网络 / API key"
          style={{ marginTop: 12 }}
        />
      )}
    </Card>
  );
};

export default MaterialsProjectPanel;