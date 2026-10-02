import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { DiagnosisIssue, RecommendedFix } from '../../types/generated-api';
import RepairSuggestions from './RepairSuggestions';

const issue: DiagnosisIssue = {
  issue_id: 'SCF-1', rule_id: 'SCF_RULE', severity: 'high', category: 'electronic_convergence',
  title: '电子步未收敛', summary: '达到最大步数', evidence: [], possible_causes: [], recommendations: [],
  auto_fixable: true, confidence: 0.9, blocking: true,
};

const fix: RecommendedFix = {
  fix_id: 'fix-1', issue_ids: ['SCF-1'], target_file: 'INCAR', strategy: 'parameter_patch',
  fix_status: 'generated', safe_to_generate: true, requires_user_confirmation: true,
  changes: [
    { parameter: 'ALGO', operation: 'replace', old_value: 'Fast', new_value: 'Normal', reason: '提高稳健性' },
    { parameter: 'NELM', operation: 'add', old_value: null, new_value: 300, reason: '增加步数' },
    { parameter: 'ICHARG', operation: 'remove', old_value: 2, new_value: null, reason: '移除覆盖值' },
  ],
  diff: '- ALGO = Fast\n+ ALGO = Normal\n', generated_file_id: 'file-1', warnings: ['这是建议，不保证适用于所有体系。'],
  reason_code: 'candidate_ready', reason: '候选参数已核验。', manual_steps: ['先备份输入文件。'],
};

describe('RepairSuggestions', () => {
  it('explains a verified candidate and shows its actual before/after changes and linked issue', () => {
    render(<RepairSuggestions fixes={[fix]} issues={[issue]} fixAvailable />);

    expect(screen.getByText('候选修复已生成，应用前请审阅')).toBeInTheDocument();
    expect(screen.getByText(/关联问题：电子步未收敛/)).toBeInTheDocument();
    expect(screen.getAllByText((_, element) => element?.textContent === '目标文件：INCAR').length).toBeGreaterThan(0);
    expect(screen.getByText('Fast')).toBeInTheDocument();
    expect(screen.getByText('Normal')).toBeInTheDocument();
    expect(screen.getByText('未设置')).toBeInTheDocument();
    expect(screen.queryByText('null')).not.toBeInTheDocument();
    expect(screen.getByText('新增')).toBeInTheDocument();
    expect(screen.getByText('修改')).toBeInTheDocument();
    expect(screen.getByText('移除', { selector: 'td' })).toBeInTheDocument();
    expect(screen.getByText(/不会自动应用参数/)).toBeInTheDocument();
    expect(screen.getByText(/风险提示/)).toBeInTheDocument();
  });

  it('does not render empty tables or diff blocks and does not inherit stale safe/generated flags', () => {
    const staleFix = { ...fix, fix_status: 'generated' as const, safe_to_generate: true, reason_code: 'candidate_invalid', changes: [], diff: '' };
    render(<RepairSuggestions fixes={[staleFix]} issues={[issue]} fixAvailable={false} reasonCode="candidate_invalid" reason="候选与有效修改不一致。" />);

    expect(screen.getByText('建议人工处理')).toBeInTheDocument();
    expect(screen.getByText('候选与有效修改不一致。')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByText('查看候选差异')).not.toBeInTheDocument();
    expect(screen.queryByText('建议修改，当前无可用候选')).not.toBeInTheDocument();
    expect(screen.queryByText('fix-1')).not.toBeInTheDocument();
    expect(screen.queryByText(/目标文件/)).not.toBeInTheDocument();
  });

  it.each([
    ['missing_incar', '需补充信息'],
    ['missing_value', '需补充信息'],
    ['manual_review', '建议人工处理'],
    ['safety_rejected', '建议人工处理'],
    ['no_changes', '暂无可生成的参数修改'],
    ['unknown_future_code', '修复状态待核验'],
  ])('maps %s to a clear state and retains backend recovery guidance', (reasonCode, title) => {
    render(<RepairSuggestions fixes={[]} issues={[]} fixAvailable={false} reasonCode={reasonCode} reason="后端提供的具体原因" manualSteps={['备份后再手工处理']} />);
    expect(screen.getByText(title)).toBeInTheDocument();
    expect(screen.getByText('后端提供的具体原因')).toBeInTheDocument();
    expect(screen.getByText('备份后再手工处理')).toBeInTheDocument();
  });

  it('treats old responses without fix_available as unverifiable and avoids rendering empty recommendations', () => {
    const emptyFix = { ...fix, changes: [], diff: '', warnings: [], reason: '' };
    render(<RepairSuggestions fixes={[emptyFix]} issues={[]} />);
    expect(screen.getByText('修复状态待核验')).toBeInTheDocument();
    expect(screen.getByText(/刷新修复状态/)).toBeInTheDocument();
    expect(screen.queryByText('建议修改，当前无可用候选')).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows warning-only unavailable fixes as general notes without exposing a fix card identity or target', () => {
    const warningOnly = {
      ...fix,
      changes: [],
      diff: '',
      reason_code: 'candidate_missing',
      reason: '',
      warnings: ['缺少非空 INCAR 文件，请补充后重新诊断。'],
      manual_steps: ['上传原始且非空的 INCAR。'],
    };
    render(<RepairSuggestions
      fixes={[warningOnly]}
      issues={[]}
      fixAvailable={false}
      reasonCode="missing_incar"
      reason="请上传原始且非空的 INCAR 文件后重新诊断。"
      manualSteps={['确认上传的是本次计算使用的文件。']}
    />);

    expect(screen.getByText('需补充信息')).toBeInTheDocument();
    expect(screen.getByText(/缺少非空/)).toBeInTheDocument();
    expect(screen.getByText('上传原始且非空的 INCAR。')).toBeInTheDocument();
    expect(screen.queryByText('fix-1')).not.toBeInTheDocument();
    expect(screen.queryByText(/目标文件/)).not.toBeInTheDocument();
    expect(screen.queryByText('建议修改，尚未生成候选')).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});
