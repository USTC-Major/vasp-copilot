// ============================================================
// ParameterPatchEditor — 白名单参数编辑（全量列举 + 当前值 + 点击选中修改）
// ============================================================

import React, { useMemo, useState } from 'react';
import { Card, Table, Input, Select, Switch, Button, Space, Alert, Modal, Tag, Typography } from 'antd';
import { SafetyOutlined, FormOutlined } from '@ant-design/icons';
import type { ParameterPatch } from '../../types/generated-api';

interface AllowedParam {
  parameter: string;
  type: string;
  minimum?: number;
  maximum?: number;
  options?: string[];
}

interface ParameterPatchEditorProps {
  patches: ParameterPatch[];
  allowedParams: AllowedParam[];
  currentValues?: Record<string, string | number | boolean>;
  onPatchesChange: (patches: ParameterPatch[]) => void;
}

interface LocalRow {
  key: string;
  parameter: string;
  paramType: string;
  options?: string[];
  currentValue: string;
  selected: boolean;
  newValue: string;
  reason: string;
}

const { Text } = Typography;

function parseValue(raw: string, paramType: string): string | number | boolean {
  if (paramType === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (paramType === 'boolean') return raw === 'true';
  return raw;
}

const ParameterPatchEditor: React.FC<ParameterPatchEditorProps> = ({
  patches,
  allowedParams,
  currentValues,
  onPatchesChange,
}) => {
  const seedRows = useMemo<LocalRow[]>(() => {
    const existing: Record<string, ParameterPatch> = {};
    for (const p of patches) {
      if (p.operation === 'replace' && !existing[p.parameter]) existing[p.parameter] = p;
    }
    return allowedParams.map((p, i) => {
      const current = currentValues?.[p.parameter];
      const currentValue = current !== undefined && current !== null ? String(current) : '';
      const patch = existing[p.parameter];
      return {
        key: `param_${p.parameter}_${i}`,
        parameter: p.parameter,
        paramType: p.type,
        options: p.options,
        currentValue,
        selected: !!patch,
        newValue: patch ? String(patch.value) : '',
        reason: patch?.reason || '',
      };
    });
  }, [allowedParams, patches, currentValues]);

  const [localRows, setLocalRows] = useState<LocalRow[]>(seedRows);
  const [showWarning, setShowWarning] = useState(false);

  const buildPatches = (rows: LocalRow[]): ParameterPatch[] => {
    const result: ParameterPatch[] = [];
    rows.forEach((row, idx) => {
      if (!row.selected) return;
      if (row.newValue === '' || row.newValue === row.currentValue) return;
      result.push({
        patch_id: `user_${row.parameter}_${idx}_${Date.now().toString(36)}`,
        composition_id: '',
        expected_revision: 1,
        parameter: row.parameter,
        operation: 'replace',
        value: parseValue(row.newValue, row.paramType),
        source: 'user',
        reason: row.reason || `用户修改 ${row.parameter}`,
        confirmed_by_user: true,
        validation: { allowed: true, rule_ids: [], warnings: [] },
      });
    });
    return result;
  };

  const commitRows = (next: LocalRow[]) => {
    setLocalRows(next);
    onPatchesChange(buildPatches(next));
  };

  const toggleRow = (key: string, selected: boolean) => {
    commitRows(
      localRows.map((r) =>
        r.key === key
          ? { ...r, selected, newValue: selected && r.newValue === '' ? r.currentValue : r.newValue }
          : r
      )
    );
  };

  const updateField = (key: string, field: 'newValue' | 'reason', value: string) => {
    commitRows(localRows.map((r) => (r.key === key ? { ...r, [field]: value } : r)));
  };

  const modifiedCount = localRows.filter(
    (r) => r.selected && r.newValue !== '' && r.newValue !== r.currentValue
  ).length;

  const columns = [
    {
      title: '参数',
      dataIndex: 'parameter',
      width: 200,
      render: (_: string, record: LocalRow) => (
        <Space size={8}>
          <Text strong>{record.parameter}</Text>
          <Tag color="blue" style={{ fontSize: 10 }}>
            {record.paramType}
          </Tag>
        </Space>
      ),
    },
    {
      title: '当前值',
      dataIndex: 'currentValue',
      width: 160,
      render: (v: string) => (v === '' ? <Text type="secondary">—</Text> : <Text code>{v}</Text>),
    },
    {
      title: '选中修改',
      dataIndex: 'selected',
      width: 110,
      render: (_: unknown, record: LocalRow) => (
        <Switch
          size="small"
          checked={record.selected}
          onChange={(checked) => toggleRow(record.key, checked)}
        />
      ),
    },
    {
      title: '修改为',
      dataIndex: 'newValue',
      width: 180,
      render: (_: string, record: LocalRow) =>
        record.options && record.options.length > 0 ? (
          <Select
            style={{ width: '100%' }}
            size="small"
            disabled={!record.selected}
            value={record.newValue || undefined}
            placeholder={record.currentValue || '选择值'}
            onChange={(v) => updateField(record.key, 'newValue', v)}
            options={record.options.map((o) => ({ label: o, value: o }))}
          />
        ) : (
          <Input
            size="small"
            disabled={!record.selected}
            value={record.newValue}
            placeholder={record.currentValue || '输入新值'}
            onChange={(e) => updateField(record.key, 'newValue', e.target.value)}
          />
        ),
    },
    {
      title: '原因',
      dataIndex: 'reason',
      render: (_: string, record: LocalRow) => (
        <Input
          size="small"
          disabled={!record.selected}
          value={record.reason}
          placeholder="修改原因（可选）"
          onChange={(e) => updateField(record.key, 'reason', e.target.value)}
        />
      ),
    },
  ];

  return (
    <Card
      title={<span><SafetyOutlined /> 参数白名单编辑</span>}
      bordered={false}
    >
      <Alert
        type="info"
        showIcon
        icon={<FormOutlined />}
        message="参数编辑仅限白名单"
        description="表中已列出所有可修改参数及其当前值。点击“选中修改”后可编辑新值，保存的修改将作为参数补丁（patch）记录来源 (provenance)。"
        style={{ marginBottom: 12 }}
      />

      <Table
        dataSource={localRows}
        columns={columns}
        rowKey="key"
        pagination={false}
        size="small"
        locale={{ emptyText: '暂无可编辑参数' }}
        rowClassName={(record) => (record.selected ? 'ant-table-row-selected-param' : '')}
      />

      <Space style={{ marginTop: 12, justifyContent: 'space-between', width: '100%' }}>
        <Space>
          <Text type="secondary">
            {modifiedCount > 0 ? `已选择 ${modifiedCount} 项修改` : '未修改任何参数'}
          </Text>
        </Space>
        <Button danger onClick={() => setShowWarning(true)}>
          禁止粘贴完整 INCAR
        </Button>
      </Space>

      {/* 防止粘贴完整 INCAR 的警告弹窗 */}
      <Modal
        title="操作禁止"
        open={showWarning}
        onOk={() => setShowWarning(false)}
        onCancel={() => setShowWarning(false)}
      >
        <Alert
          type="error"
          showIcon
          message="禁止直接粘贴完整 INCAR 文本"
          description={
            <div>
              <p>本项目采用白名单参数编辑方式，只能修改经过 Recipe 系统允许的参数：</p>
              <ul>
                <li>所有参数修改通过结构化 patch 提交</li>
                <li>每个修改都有来源记录 (provenance)</li>
                <li>参数被校验器检查后才可写入最终 INCAR</li>
              </ul>
              <p>如需修改参数，请在表格中选中对应参数并修改“修改为”值。</p>
            </div>
          }
        />
      </Modal>
    </Card>
  );
};

export default ParameterPatchEditor;