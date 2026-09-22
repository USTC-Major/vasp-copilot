import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Checkbox, Col, Divider, Empty, Input, InputNumber, List, Row, Select, Space, Tag, Typography, message } from 'antd';
import { ArrowLeftOutlined, CloudUploadOutlined, FileSearchOutlined, FolderOpenOutlined, PlusOutlined, SafetyCertificateOutlined, SendOutlined } from '@ant-design/icons';
import { Link, useParams } from 'react-router-dom';
import ToolboxTaskStatus from '../components/toolbox/ToolboxTaskStatus';
import AiDirectoryPicker from '../components/ai/AiDirectoryPicker';
import { toolboxApi } from '../api/client';
import { useToolboxRunTool, useToolboxTaskDetail } from '../hooks/useApi';
import type { ToolboxIncarEntry, ToolboxJob, ToolboxPlanJobInput } from '../types/toolbox';

const { Title, Text, Paragraph } = Typography;

const freshPlanJob = (index: number): ToolboxPlanJobInput => ({
  key: index === 0 ? 'static' : `job_${index + 1}`,
  label: index === 0 ? '静态计算' : `计算步骤 ${index + 1}`,
  kind: index === 0 ? 'static' : 'custom',
  requires: [],
  description: '',
});

const parseParameterValue = (raw: string): ToolboxIncarEntry['value'] => {
  const trimmed = raw.trim();
  if (/^(true|\.true\.)$/i.test(trimmed)) return true;
  if (/^(false|\.false\.)$/i.test(trimmed)) return false;
  if (/^-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(trimmed)) return Number(trimmed);
  if (trimmed.includes(',')) {
    const parts = trimmed.split(',').map((part) => part.trim()).filter(Boolean);
    return parts.every((part) => Number.isFinite(Number(part))) ? parts.map(Number) : parts;
  }
  return trimmed;
};

const ToolboxTaskPage: React.FC = () => {
  const { projectId = '', taskId = '' } = useParams();
  const detailQuery = useToolboxTaskDetail(projectId, taskId);
  const runToolMutation = useToolboxRunTool();
  const detail = detailQuery.data;
  const jobs = useMemo(() => detail?.flow.jobs ?? [], [detail?.flow.jobs]);
  const artifactEntries = useMemo(() => Object.entries(detail?.flow.artifacts ?? {}), [detail?.flow.artifacts]);
  const [lastResult, setLastResult] = useState('');
  const [strategy, setStrategy] = useState('按依赖顺序完成基础计算');
  const [planJobs, setPlanJobs] = useState<ToolboxPlanJobInput[]>([freshPlanJob(0)]);
  const [selectedJobs, setSelectedJobs] = useState<string[]>([]);
  const [copyArtifacts, setCopyArtifacts] = useState<string[]>([]);
  const [copyJob, setCopyJob] = useState('');
  const [incarJob, setIncarJob] = useState('');
  const [incarEntries, setIncarEntries] = useState([{ tag: 'ENCUT', value: '520' }]);
  const [kpointJob, setKpointJob] = useState('');
  const [kGrid, setKGrid] = useState<[number, number, number]>([4, 4, 4]);
  const [centering, setCentering] = useState<'Gamma' | 'Monkhorst-Pack'>('Gamma');
  const [uploadArtifact, setUploadArtifact] = useState('');
  const [uploadJob, setUploadJob] = useState('');
  const [editTitle, setEditTitle] = useState('');
  const [editGoal, setEditGoal] = useState('');
  const [editLocal, setEditLocal] = useState('');
  const [editHpc, setEditHpc] = useState('');
  const [savingMetadata, setSavingMetadata] = useState(false);
  const [pickerKind, setPickerKind] = useState<'local' | 'hpc' | null>(null);
  const [browseKind, setBrowseKind] = useState<'local' | 'hpc'>('local');
  const [browseAction, setBrowseAction] = useState<'list' | 'read'>('list');
  const [browsePath, setBrowsePath] = useState('');
  const [mpFormula, setMpFormula] = useState('');
  const [mpLimit, setMpLimit] = useState(10);
  const [mpRows, setMpRows] = useState<Record<string, unknown>[]>([]);
  const [mpJob, setMpJob] = useState('');
  const [authorizationJobKey, setAuthorizationJobKey] = useState('');

  const jobOptions = useMemo(() => jobs.map((job) => ({ value: job.key, label: `${job.label || job.key} (${job.status})` })), [jobs]);
  const artifactOptions = useMemo(() => artifactEntries.map(([id, artifact]) => ({ value: id, label: `${artifact.name} · ${artifact.path}` })), [artifactEntries]);
  const authorizationIdentity = useMemo(
    () => jobs.map((job) => `${job.key}:${job.attempt_id ?? ''}`).join('|'),
    [jobs],
  );
  const authorizationJob = useMemo(() => {
    if (jobs.length === 1) return jobs[0];
    return jobs.find((job) => job.key === authorizationJobKey);
  }, [authorizationJobKey, jobs]);
  const selectedAuthorizationAttempt = authorizationJob?.attempt_id;
  const requiresExplicitAttempt = jobs.length > 1;
  const authorizationReady = !!authorizationJob && (!requiresExplicitAttempt || !!selectedAuthorizationAttempt);
  const authorizationArgs = (job: ToolboxJob): Record<string, unknown> => {
    if (job.attempt_id) return { job_key: job.key, attempt_id: job.attempt_id };
    // The frozen backend contract keeps the old empty single-job call valid.
    return jobs.length === 1 ? {} : { job_key: job.key };
  };
  const retryArgs = (job: ToolboxJob): Record<string, unknown> => (
    job.attempt_id ? { job_key: job.key, attempt_id: job.attempt_id } : { job_key: job.key }
  );

  // Only initialize editable metadata when entering another task. Monitoring and
  // tool activity can update the server timestamp every five seconds; using that
  // timestamp here would overwrite an in-progress edit.
  useEffect(() => {
    if (!detail?.task) return;
    setEditTitle(detail.task.title ?? '');
    setEditGoal(detail.task.goal ?? '');
    setEditLocal(detail.task.local_workspace ?? '');
    setEditHpc(detail.task.hpc_workspace ?? '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.task?.id]);

  // A job key alone is not an authorization identity. Drop local selection and
  // receipt whenever the route or the server's current attempt set changes.
  useEffect(() => {
    setAuthorizationJobKey('');
    setLastResult('');
  }, [projectId, taskId, authorizationIdentity]);

  const runTool = async (name: string, args: Record<string, unknown> = {}) => {
    try {
      const response = await runToolMutation.mutateAsync({ projectId, taskId, name, args });
      setLastResult(response.result || '请求已处理，请查看最新执行状态。');
      if (response.pending) {
        message.info('已生成一次性人工确认卡；操作尚未执行。');
      } else if (name === 'submit') {
        message.info('提交请求已处理；是否真实提交请以确认卡、submission_state 和作业号为准。');
      } else if (name === 'get_state') {
        message.success('已更新输入与任务状态；完整回执显示在页面中。');
      } else {
        message.success('请求已处理；完整回执显示在页面中。');
      }
      await detailQuery.refetch();
      return response;
    } catch (error) {
      const text = error instanceof Error ? error.message : '操作失败';
      setLastResult(text);
      message.error(text);
      await detailQuery.refetch();
      return null;
    }
  };

  const submitPlan = () => {
    const normalized = planJobs
      .map((job) => ({ ...job, key: job.key.trim(), label: job.label.trim(), kind: job.kind.trim(), requires: job.requires?.filter(Boolean) }))
      .filter((job) => job.key && job.label && job.kind);
    if (!normalized.length) {
      message.warning('至少填写一个完整计算步骤');
      return;
    }
    void runTool('plan', { strategy: strategy.trim(), jobs: normalized });
  };

  const updatePlanJob = (index: number, patch: Partial<ToolboxPlanJobInput>) => {
    setPlanJobs((current) => current.map((job, i) => i === index ? { ...job, ...patch } : job));
  };

  const stopMonitor = async () => {
    await runTool('stop_monitor');
    message.warning('已请求停止跟踪；远端作业可能继续运行。');
  };

  const saveMetadata = async () => {
    if (!editTitle.trim() || !editLocal.trim()) {
      message.warning('任务标题与本地工作区不能为空');
      return;
    }
    setSavingMetadata(true);
    try {
      await toolboxApi.updateTask(projectId, taskId, {
        title: editTitle.trim(), goal: editGoal.trim(), local_workspace: editLocal.trim(), hpc_workspace: editHpc.trim(),
      });
      message.success('任务信息与工作区已更新');
      await detailQuery.refetch();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '更新任务失败');
    } finally {
      setSavingMetadata(false);
    }
  };

  const inspectWorkspace = () => {
    if (browseAction === 'read' && !browsePath.trim()) {
      message.warning('请输入工作区内的相对文件路径');
      return;
    }
    const name = browseKind === 'local'
      ? (browseAction === 'list' ? 'ws_list' : 'ws_read')
      : (browseAction === 'list' ? 'hpc_list' : 'hpc_read');
    const args = browseAction === 'read' || (browseKind === 'hpc' && browsePath.trim()) ? { path: browsePath.trim() } : {};
    void runTool(name, args);
  };

  const searchMaterials = async () => {
    if (!mpFormula.trim()) {
      message.warning('请输入化学式');
      return;
    }
    const response = await runTool('mp_search', { formula: mpFormula.trim(), limit: mpLimit });
    if (!response) return;
    try {
      const parsed = JSON.parse(response.result) as { materials?: Record<string, unknown>[] };
      setMpRows(Array.isArray(parsed.materials) ? parsed.materials : []);
    } catch {
      setMpRows([]);
    }
  };

  if (detailQuery.isError && !detail) {
    const error = detailQuery.error;
    const missing = typeof error === 'object' && error !== null && 'status' in error && error.status === 404;
    return (
      <Alert
        type={missing ? 'warning' : 'error'}
        showIcon
        message={missing ? '指定的 Toolbox 任务不存在' : '无法读取 Toolbox 任务'}
        description={<Space direction="vertical"><Text>{missing ? '任务可能已删除，页面不会自动选择同项目的其他任务。' : '请确认 8000 服务可用。'}</Text><Link to="/toolbox/projects">返回项目列表</Link></Space>}
      />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Link to="/toolbox/projects"><ArrowLeftOutlined /> 返回项目与任务</Link>
        <Title level={2} style={{ margin: '10px 0 4px' }}>{detail?.task.title || 'Toolbox 计算任务'}</Title>
        <Paragraph type="secondary" style={{ margin: 0 }}>{detail?.task.goal || '使用结构化表单准备并执行计算，不需要模型。'}</Paragraph>
      </div>

      <Card title="任务信息与工作区" size="small">
        <Row gutter={[12, 12]}>
          <Col xs={24} md={8}><label htmlFor="task-edit-title"><Text strong>任务标题</Text></label><Input id="task-edit-title" value={editTitle} onChange={(event) => setEditTitle(event.target.value)} /></Col>
          <Col xs={24} md={16}><label htmlFor="task-edit-goal"><Text strong>计算目标</Text></label><Input id="task-edit-goal" value={editGoal} onChange={(event) => setEditGoal(event.target.value)} /></Col>
          <Col xs={24} md={12}>
            <label htmlFor="task-edit-local"><Text strong>本地工作区</Text></label>
            <Space.Compact style={{ width: '100%' }}><Input id="task-edit-local" value={editLocal} onChange={(event) => setEditLocal(event.target.value)} /><Button icon={<FolderOpenOutlined />} onClick={() => setPickerKind('local')}>浏览</Button></Space.Compact>
          </Col>
          <Col xs={24} md={12}>
            <label htmlFor="task-edit-hpc"><Text strong>超算工作区</Text></label>
            <Space.Compact style={{ width: '100%' }}><Input id="task-edit-hpc" value={editHpc} onChange={(event) => setEditHpc(event.target.value)} placeholder="可留空" /><Button icon={<FolderOpenOutlined />} onClick={() => setPickerKind('hpc')}>浏览</Button></Space.Compact>
          </Col>
          <Col span={24}><Button type="primary" loading={savingMetadata} onClick={() => void saveMetadata()}>保存任务信息</Button></Col>
        </Row>
      </Card>

      <ToolboxTaskStatus
        projectId={projectId}
        taskId={taskId}
        onStopMonitor={stopMonitor}
        stopPending={runToolMutation.isPending}
      />

      {lastResult && <Alert type="info" showIcon message="最近一次工具回执" description={lastResult} closable onClose={() => setLastResult('')} />}

      <Card title="1. 结构化计算规划" extra={<Tag color="blue">仅规划，不提交</Tag>}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <div><label htmlFor="toolbox-strategy"><Text strong>执行策略</Text></label><Input id="toolbox-strategy" value={strategy} onChange={(event) => setStrategy(event.target.value)} /></div>
          {planJobs.map((job, index) => (
            <Card key={index} size="small" title={`步骤 ${index + 1}`} extra={planJobs.length > 1 ? <Button danger type="link" onClick={() => setPlanJobs((items) => items.filter((_, i) => i !== index))}>删除</Button> : undefined}>
              <Row gutter={[10, 10]}>
                <Col xs={24} sm={8}><label><Text strong>标识</Text><Input aria-label={`步骤 ${index + 1} 标识`} value={job.key} onChange={(event) => updatePlanJob(index, { key: event.target.value })} placeholder="static" /></label></Col>
                <Col xs={24} sm={8}><label><Text strong>名称</Text><Input aria-label={`步骤 ${index + 1} 名称`} value={job.label} onChange={(event) => updatePlanJob(index, { label: event.target.value })} placeholder="静态计算" /></label></Col>
                <Col xs={24} sm={8}><label><Text strong>类型</Text><Input aria-label={`步骤 ${index + 1} 类型`} value={job.kind} onChange={(event) => updatePlanJob(index, { kind: event.target.value })} placeholder="static" /></label></Col>
                <Col span={24}><label><Text strong>依赖步骤</Text><Select aria-label={`步骤 ${index + 1} 依赖`} mode="tags" style={{ width: '100%' }} value={job.requires ?? []} onChange={(value) => updatePlanJob(index, { requires: value })} placeholder="选择或输入上游步骤标识" options={planJobs.filter((_, i) => i !== index).map((candidate) => ({ value: candidate.key, label: candidate.label || candidate.key }))} /></label></Col>
              </Row>
            </Card>
          ))}
          <Space wrap>
            <Button icon={<PlusOutlined />} onClick={() => setPlanJobs((items) => [...items, freshPlanJob(items.length)])}>添加步骤</Button>
            <Button type="primary" icon={<SendOutlined />} loading={runToolMutation.isPending} onClick={submitPlan}>保存计算计划</Button>
          </Space>
        </Space>
      </Card>

      <Card title="2. 输入登记与作业选择">
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap>
            <Button icon={<FileSearchOutlined />} loading={runToolMutation.isPending} onClick={() => void runTool('get_state')}>扫描并登记现有输入</Button>
            <Text type="secondary">只记录文件元数据与状态，不写入目录。</Text>
          </Space>
          {artifactEntries.length ? (
            <List
              size="small"
              bordered
              header={<Text strong>已登记输入（{artifactEntries.length}）</Text>}
              dataSource={artifactEntries}
              renderItem={([id, artifact]) => (
                <List.Item>
                  <List.Item.Meta
                    title={artifact.name || id}
                    description={<Space direction="vertical" size={0}>
                      <Text type="secondary" style={{ overflowWrap: 'anywhere' }}>{artifact.path}</Text>
                      <Text type="secondary">{artifact.size === undefined ? '大小未知' : `${artifact.size} 字节`}{artifact.sha256 ? ` · SHA-256 ${artifact.sha256}` : ''}</Text>
                    </Space>}
                  />
                </List.Item>
              )}
            />
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未登记输入，请先扫描工作区" />}
          <Card size="small" title="有界查看工作区文件">
            <Space wrap style={{ width: '100%' }}>
              <Select aria-label="工作区位置" value={browseKind} onChange={setBrowseKind} options={[{ value: 'local', label: '本地工作区' }, { value: 'hpc', label: '超算工作区' }]} style={{ minWidth: 140 }} />
              <Select aria-label="查看方式" value={browseAction} onChange={setBrowseAction} options={[{ value: 'list', label: '列出目录' }, { value: 'read', label: '读取文本文件' }]} style={{ minWidth: 150 }} />
              <Input aria-label="工作区相对路径" value={browsePath} onChange={(event) => setBrowsePath(event.target.value)} placeholder={browseAction === 'read' ? '相对文件路径，如 static/INCAR' : '超算子目录可选；本地列目录使用根目录'} style={{ flex: 1, minWidth: 260 }} />
              <Button onClick={inspectWorkspace}>查看</Button>
            </Space>
            <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>路径受任务工作区边界限制；敏感文件与越界路径由执行服务拒绝。</Text>
          </Card>
          {jobs.length ? (
            <div>
              <Text strong>本次参与后续准备的作业</Text>
              <Checkbox.Group
                style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 8 }}
                options={jobs.map((job) => ({ value: job.key, label: job.label || job.key }))}
                value={selectedJobs}
                onChange={(values) => setSelectedJobs(values as string[])}
              />
              <Button style={{ marginTop: 10 }} onClick={() => void runTool('select_jobs', {
                submit: selectedJobs,
                skip: jobs.map((job) => job.key).filter((key) => !selectedJobs.includes(key)),
              })}>应用作业选择</Button>
              <Button type="link" onClick={() => void runTool('select_jobs', { submit_all: true })}>全部参与</Button>
              <Button type="link" danger onClick={() => void runTool('select_jobs', { skip_all: true })}>全部跳过</Button>
            </div>
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="保存计划后可选择作业" />}
        </Space>
      </Card>

      <Card title="3. 输入准备" extra={<Text type="secondary">每次写入或上传均需单次确认</Text>}>
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card size="small" title="复制已登记输入">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Select aria-label="要复制的输入" mode="multiple" value={copyArtifacts} onChange={setCopyArtifacts} options={artifactOptions} placeholder="选择一个或多个已登记输入" style={{ width: '100%' }} />
                <Select aria-label="复制到作业" value={copyJob || undefined} onChange={setCopyJob} options={jobOptions} placeholder="目标作业" style={{ width: '100%' }} />
                <Button disabled={!copyArtifacts.length || !copyJob} onClick={() => void runTool('copy_inputs', { artifact_ids: copyArtifacts, job_key: copyJob })}>生成复制确认卡</Button>
              </Space>
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card size="small" title="上传已登记输入">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Select aria-label="上传输入" value={uploadArtifact || undefined} onChange={setUploadArtifact} options={artifactOptions} placeholder="选择已登记输入" style={{ width: '100%' }} />
                <Select aria-label="上传到作业" value={uploadJob || undefined} onChange={setUploadJob} options={jobOptions} placeholder="目标作业" style={{ width: '100%' }} />
                <Button icon={<CloudUploadOutlined />} disabled={!uploadArtifact || !uploadJob} onClick={() => void runTool('hpc_upload', { artifact_id: uploadArtifact, job_key: uploadJob })}>生成上传确认卡</Button>
              </Space>
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card size="small" title="INCAR 参数键值编辑">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Select aria-label="INCAR 目标作业" value={incarJob || undefined} onChange={setIncarJob} options={jobOptions} placeholder="目标作业" style={{ width: '100%' }} />
                {incarEntries.map((entry, index) => (
                  <Space.Compact key={index} style={{ width: '100%' }}>
                    <Input aria-label={`INCAR 参数 ${index + 1} 名称`} value={entry.tag} onChange={(event) => setIncarEntries((items) => items.map((item, i) => i === index ? { ...item, tag: event.target.value.toUpperCase() } : item))} placeholder="ENCUT" style={{ width: '35%' }} />
                    <Input aria-label={`INCAR 参数 ${index + 1} 值`} value={entry.value} onChange={(event) => setIncarEntries((items) => items.map((item, i) => i === index ? { ...item, value: event.target.value } : item))} placeholder="520" />
                    <Button danger disabled={incarEntries.length === 1} onClick={() => setIncarEntries((items) => items.filter((_, i) => i !== index))}>删除</Button>
                  </Space.Compact>
                ))}
                <Space wrap>
                  <Button icon={<PlusOutlined />} onClick={() => setIncarEntries((items) => [...items, { tag: '', value: '' }])}>添加参数</Button>
                  <Button type="primary" disabled={!incarJob || incarEntries.some((entry) => !entry.tag.trim() || !entry.value.trim())} onClick={() => void runTool('propose_incar', {
                    job_key: incarJob,
                    entries: incarEntries.map((entry) => ({ tag: entry.tag.trim(), value: parseParameterValue(entry.value) })),
                  })}>预览并请求确认</Button>
                </Space>
              </Space>
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card size="small" title="KPOINTS 网格">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Select aria-label="KPOINTS 目标作业" value={kpointJob || undefined} onChange={setKpointJob} options={jobOptions} placeholder="目标作业" style={{ width: '100%' }} />
                <Space wrap>
                  {kGrid.map((value, index) => <InputNumber key={index} aria-label={`KPOINTS 网格 ${index + 1}`} min={1} precision={0} value={value} onChange={(next) => setKGrid((grid) => grid.map((item, i) => i === index ? Number(next ?? item) : item) as [number, number, number])} />)}
                </Space>
                <Select aria-label="KPOINTS 中心" value={centering} onChange={setCentering} options={[{ value: 'Gamma', label: 'Gamma' }, { value: 'Monkhorst-Pack', label: 'Monkhorst-Pack' }]} />
                <Button type="primary" disabled={!kpointJob} onClick={() => void runTool('generate_kpoints', { job_key: kpointJob, grid: kGrid, centering })}>预览并请求确认</Button>
              </Space>
            </Card>
          </Col>
        </Row>
      </Card>

      <Card title="4. Materials Project 结构导入" extra={<Tag>既有只读搜索能力</Tag>}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap>
            <Input aria-label="Materials Project 化学式" value={mpFormula} onChange={(event) => setMpFormula(event.target.value)} placeholder="化学式，如 Si 或 Fe2O3" style={{ width: 220 }} />
            <InputNumber aria-label="Materials Project 结果数" min={1} max={20} value={mpLimit} onChange={(value) => setMpLimit(Number(value ?? 10))} />
            <Button onClick={() => void searchMaterials()}>搜索候选结构</Button>
            <Select aria-label="POSCAR 导入目标作业" value={mpJob || undefined} onChange={setMpJob} options={jobOptions} placeholder="POSCAR 目标作业（可选）" style={{ minWidth: 220 }} />
          </Space>
          {mpRows.length > 0 && (
            <List
              size="small"
              dataSource={mpRows}
              renderItem={(row, index) => {
                const materialId = String(row.material_id ?? row.materialId ?? row.id ?? '');
                const title = String((row.formula_pretty ?? row.formula ?? materialId) || `候选 ${index + 1}`);
                const detailText = [row.symmetry, row.energy_above_hull, row.band_gap].filter((value) => value !== undefined).map(String).join(' · ');
                return <List.Item actions={[<Button key="import" disabled={!materialId} onClick={() => void runTool('mp_import_poscar', { material_id: materialId, ...(mpJob ? { job_key: mpJob } : {}) })}>预览并请求写入确认</Button>]}><List.Item.Meta title={title} description={<><Text code>{materialId}</Text>{detailText && <Text type="secondary"> · {detailText}</Text>}</>} /></List.Item>;
              }}
            />
          )}
          <Text type="secondary">搜索不会写文件；选择候选后会生成绑定材料 ID、目标与内容哈希的一次性确认卡。</Text>
        </Space>
      </Card>

      <Card title="5. 脚本认领、预检与提交">
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="Toolbox 不生成提交脚本"
          description="请先把自己提供的脚本放入工作区。首次“认领脚本”会生成脚本指纹确认卡；确认后再次认领，经过硬预检才会生成提交确认卡。"
        />
        <Space direction="vertical" size="small" style={{ width: '100%', marginBottom: 12 }}>
          <Select
            aria-label="提交授权计算"
            value={jobs.length === 1 ? jobs[0]?.key : (authorizationJobKey || undefined)}
            onChange={setAuthorizationJobKey}
            options={jobs.map((job) => ({ value: job.key, label: `${job.label || job.key}${job.attempt_id ? ` · 尝试 ${job.attempt_id}` : ' · 旧单计算兼容'}` }))}
            disabled={!jobs.length || jobs.length === 1}
            placeholder={jobs.length > 1 ? '请先选择一个计算' : '尚无计算'}
            style={{ maxWidth: 520 }}
          />
          {jobs.length > 1 && !authorizationJob && <Text type="warning">多个计算必须先选择一个具有当前尝试身份的计算；不会默认代为选择。</Text>}
          {requiresExplicitAttempt && authorizationJob && !selectedAuthorizationAttempt && <Text type="danger">此计算缺少当前尝试身份，不能发送准备或提交请求。</Text>}
          {authorizationJob && (
            <Space wrap>
              <Tag>计算：{authorizationJob.label || authorizationJob.key}</Tag>
              {selectedAuthorizationAttempt ? <Tag>尝试：{selectedAuthorizationAttempt}</Tag> : <Tag>旧单计算兼容参数</Tag>}
              <Text type="secondary">预检：{authorizationJob.precheck ? (authorizationJob.precheck.ok ? '通过' : '未通过') : '尚未运行'}</Text>
              <Text type="secondary">草稿：{authorizationJob.draft?.dir ?? authorizationJob.draft?.directory ?? '尚未生成'}</Text>
            </Space>
          )}
        </Space>
        <Space wrap>
          <Button disabled={!authorizationReady} onClick={() => authorizationJob && void runTool('draft', authorizationArgs(authorizationJob))}>认领脚本 / 生成草稿</Button>
          <Button disabled={!authorizationReady} icon={<FileSearchOutlined />} onClick={() => authorizationJob && void runTool('precheck', authorizationArgs(authorizationJob))}>运行硬预检</Button>
          <Button disabled={!authorizationReady} type="primary" icon={<SafetyCertificateOutlined />} onClick={() => authorizationJob && void runTool('submit', authorizationArgs(authorizationJob))}>请求提交确认</Button>
          <Button onClick={() => void runTool('report')}>汇总确定性报告</Button>
        </Space>
        <Divider />
        <Text type="secondary">
          请求已发送不代表提交成功，请以调度器作业号和提交回执为准。
        </Text>
      </Card>

      <Card title="6. 失败诊断与恢复">
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="恢复不会自动修改参数、覆盖输出或重新提交" description="先读取确定性诊断；仅明确 failed / not_converged 且有诊断证据的作业可请求恢复确认。unknown 或在途作业不能重提。" />
        {jobs.length ? (
          <List
            size="small"
            dataSource={jobs}
            renderItem={(job) => {
              const retryAllowed = ['failed', 'not_converged'].includes(job.status) && !!job.diagnosis && job.submission_state !== 'unknown';
              return (
                <List.Item actions={[
                  <Button key="diagnose" onClick={() => void runTool('diagnose_job', { job_key: job.key })}>诊断</Button>,
                  <Button key="retry" danger disabled={!retryAllowed} onClick={() => void runTool('retry_job', retryArgs(job))}>请求恢复确认</Button>,
                ]}>
                  <List.Item.Meta title={<Space><Text strong>{job.label || job.key}</Text><Tag>{job.status}</Tag></Space>} description={retryAllowed ? '已有终态诊断，可请求恢复到待准备状态。' : '当前状态不允许恢复；可先诊断或继续等待状态核对。'} />
                </List.Item>
              );
            }}
          />
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无可诊断作业" />}
      </Card>

      <AiDirectoryPicker
        open={pickerKind !== null}
        kind={pickerKind ?? 'local'}
        initialPath={pickerKind === 'hpc' ? editHpc : editLocal}
        onCancel={() => setPickerKind(null)}
        onSelect={(path) => {
          if (pickerKind === 'hpc') setEditHpc(path);
          else setEditLocal(path);
          setPickerKind(null);
        }}
      />
    </Space>
  );
};

export default ToolboxTaskPage;
