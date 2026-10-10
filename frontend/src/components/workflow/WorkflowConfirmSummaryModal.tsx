// ============================================================
// WorkflowConfirmSummaryModal — 最终确认摘要弹窗
//
// 展示内容与实际发送 payload 来自同一不可变快照（由调用方传入）。
// 请求进行中：确认/取消按钮禁用，禁止 maskClosable 与 Esc 关闭。
// ============================================================

import React from 'react';
import { Modal, Descriptions, Tag } from 'antd';
import type { WorkflowConfirmSnapshot } from '../../types/workflow-contract';
import CatalysisWorkflowContext from './CatalysisWorkflowContext';

interface WorkflowConfirmSummaryModalProps {
  open: boolean;
  snapshot: WorkflowConfirmSnapshot | null;
  isPending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const WorkflowConfirmSummaryModal: React.FC<WorkflowConfirmSummaryModalProps> = ({
  open,
  snapshot,
  isPending,
  onConfirm,
  onCancel,
}) => {
  const handleOk = () => {
    if (isPending || !snapshot) return;
    onConfirm();
  };

  const handleCancel = () => {
    if (isPending) return;
    onCancel();
  };

  return (
    <Modal
      open={open}
      title="最终确认：工作流参数摘要"
      width={640}
      mask={{ closable: !isPending }}
      keyboard={!isPending}
      destroyOnHidden
      onCancel={handleCancel}
      onOk={handleOk}
      okText="确认并生成工作流计划"
      cancelText="返回修改"
      confirmLoading={isPending}
      okButtonProps={{ disabled: isPending || !snapshot }}
      cancelButtonProps={{ disabled: isPending }}
    >
      {snapshot && (
        <>
        {snapshot.catalysis_binding && snapshot.surface_policy && <CatalysisWorkflowContext binding={snapshot.catalysis_binding} policy={snapshot.surface_policy} />}
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="结构">
            {snapshot.structure.formula}（{snapshot.structure.elements.join('、')}）
          </Descriptions.Item>
          <Descriptions.Item label="样品名称">{snapshot.sample_name}</Descriptions.Item>
          <Descriptions.Item label="POSCAR 首行">{snapshot.poscar_comment}</Descriptions.Item>
          <Descriptions.Item label="计算任务">
            {snapshot.requested_tasks.join(' → ')}
          </Descriptions.Item>
          <Descriptions.Item label="电子类型">{snapshot.electronic_type}</Descriptions.Item>
          <Descriptions.Item label="磁性">{snapshot.magnetic ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="SOC">{snapshot.soc ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="精度">{snapshot.precision}</Descriptions.Item>
          {snapshot.patches?.map(patch => <Descriptions.Item key={patch.patch_id} label="用户确认的步骤补丁">{patch.step_id} · {patch.parameter} = {String(patch.value)}；{patch.reason}</Descriptions.Item>)}
          <Descriptions.Item label="DFT+U">
            {snapshot.dftu.enabled ? (
              <>
                <div>
                  {snapshot.dftu.form === 'dudarev'
                    ? 'Dudarev（Ueff 输入；LDAUTYPE=2）'
                    : snapshot.dftu.form === 'liechtenstein'
                      ? 'Liechtenstein（U/J 输入；LDAUTYPE=1）'
                      : '旧版 Dudarev U/J 输入（LDAUTYPE=2，原值保留）'}
                </div>
                {snapshot.dftu.entries.map((entry, idx) => {
                  const values = snapshot.dftu.form === 'dudarev'
                    ? `Ueff=${entry.u_eff_ev} eV → LDAUU=${entry.u_eff_ev}, LDAUJ=0`
                    : `U=${entry.u_ev} eV，J=${entry.j_ev} eV → LDAUU=${entry.u_ev}, LDAUJ=${entry.j_ev}`;
                  return (
                    <div key={idx}>
                      {entry.element}：L={entry.l}，{values}
                      {entry.source_note && <span>（来源：{entry.source_note}）</span>}
                      {entry.confirmed_by_user && <Tag color="green" style={{ marginLeft: 8 }}>已由用户确认</Tag>}
                    </div>
                  );
                })}
              </>
            ) : (
              // DFT+U 关闭时后端不生成 LDAU 相关参数；“派生 L=-1/U=0/J=0”
              // 仅适用于启用 DFT+U 但部分 POSCAR 元素未配置条目的情形，不得混淆。
              '未启用（INCAR 不生成 LDAU/LDAUL/LDAUU/LDAUJ 参数）'
            )}
          </Descriptions.Item>
          <Descriptions.Item label="调度器">{snapshot.scheduler.type}</Descriptions.Item>
          <Descriptions.Item label="节点 / 每节点核数">
            {snapshot.scheduler.nodes} / {snapshot.scheduler.tasks_per_node}
          </Descriptions.Item>
          <Descriptions.Item label="Walltime">{snapshot.scheduler.walltime}</Descriptions.Item>
          <Descriptions.Item label="VASP 可执行文件">{snapshot.scheduler.vasp_binary_hint}</Descriptions.Item>
        </Descriptions>
        </>
      )}
    </Modal>
  );
};

export default WorkflowConfirmSummaryModal;
