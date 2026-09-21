import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DiagnosisIssue } from '../../types/generated-api';
import IssueCard from './IssueCard';

afterEach(cleanup);

// Real backend Issue schema has no tags field; mocks previously always had it.
const backendIssue = {
  issue_id: 'ZHEGV_LAPACK_FAILURE-0001', rule_id: 'ZHEGV_LAPACK_FAILURE',
  severity: 'high', category: 'core_errors', title: 'LAPACK 对角化失败',
  summary: 'OUTCAR 中出现对角化警告。',
  evidence: [{ file: 'OUTCAR', line: 13489, message: 'WARNING in EDDRMM: call to ZHEGV failed' }],
  recommendations: [{ action: 'review', target: 'user', rationale: '核对数值稳定性', requires_user_confirmation: true }],
  auto_fixable: false, confidence: 0.85, blocking: true, possible_causes: ['数值不稳定'],
};

describe('IssueCard real backend contract', () => {
  it.each([undefined, null, []])('renders expanded issues without usable tags: %s', (tags) => {
    const issue = JSON.parse(JSON.stringify({ ...backendIssue, tags })) as DiagnosisIssue;
    render(<IssueCard issue={issue} selected={false} onSelect={vi.fn()} />);
    expect(screen.getByText('LAPACK 对角化失败')).toBeInTheDocument();
    expect(screen.getByText('数值不稳定')).toBeInTheDocument();
    expect(screen.getByText('证据 (1 条)')).toBeInTheDocument();
    expect(screen.getByText('Rule: ZHEGV_LAPACK_FAILURE')).toBeInTheDocument();
  });

  it('preserves existing labels, evidence and selection interaction', () => {
    const onSelect = vi.fn();
    const issue = { ...backendIssue, tags: ['历史案例'] } as DiagnosisIssue;
    render(<IssueCard issue={issue} selected={false} onSelect={onSelect} />);
    expect(screen.getByText('历史案例')).toBeInTheDocument();
    fireEvent.click(screen.getByText('证据 (1 条)'));
    expect(screen.getByText('WARNING in EDDRMM: call to ZHEGV failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '选中修复' }));
    expect(onSelect).toHaveBeenCalledWith(issue.issue_id, true);
  });
});
