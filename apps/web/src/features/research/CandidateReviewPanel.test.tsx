import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { CandidateReviewPanel } from './CandidateReviewPanel';
import type { ConclusionCandidate } from '@/api/researchTimeline';

function makeCandidate(overrides: Partial<ConclusionCandidate> = {}): ConclusionCandidate {
  return {
    candidate_id: 'cand-001',
    ordinal: 0,
    statement: '温度 200°C 时收率最高',
    scope: '所有批次',
    confidence_level: 'high',
    limitations: '样本量有限',
    status: 'pending',
    ...overrides,
  };
}

function renderPanel(props: {
  extractionStatus?: string | null;
  candidates?: ConclusionCandidate[];
  onRetry?: () => void;
  onSave?: (s: { candidate_id: string; edited_statement?: string }[]) => void;
  onAddManual?: () => void;
}): void {
  render(
    <AntApp>
      <CandidateReviewPanel
        extractionStatus={props.extractionStatus ?? null}
        candidates={props.candidates ?? []}
        onRetry={props.onRetry}
        onSave={props.onSave}
        onAddManual={props.onAddManual}
      />
    </AntApp>,
  );
}

describe('CandidateReviewPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows spinner when extraction is running', () => {
    renderPanel({ extractionStatus: 'running' });
    expect(document.querySelector('.ant-spin')).toBeTruthy();
  });

  it('shows spinner when extraction is queued', () => {
    renderPanel({ extractionStatus: 'queued' });
    expect(document.querySelector('.ant-spin')).toBeTruthy();
  });

  it('shows retry button when extraction failed', () => {
    const onRetry = vi.fn();
    renderPanel({ extractionStatus: 'failed', onRetry });
    expect(screen.getByText('候选提取失败')).toBeInTheDocument();
  });

  it('shows manual add button when extraction failed', () => {
    const onAddManual = vi.fn();
    renderPanel({ extractionStatus: 'failed', onAddManual });
    expect(screen.getByRole('button', { name: '手动新增结论' })).toBeInTheDocument();
  });

  it('shows empty message when succeeded with 0 candidates', () => {
    renderPanel({ extractionStatus: 'succeeded', candidates: [] });
    expect(screen.getByText('分析结果不足以支持任何候选结论')).toBeInTheDocument();
  });

  it('renders candidates when succeeded', () => {
    renderPanel({
      extractionStatus: 'succeeded',
      candidates: [makeCandidate({ statement: '收率与温度正相关' })],
    });
    expect(screen.getByText('收率与温度正相关')).toBeInTheDocument();
  });

  it('shows candidate count', () => {
    renderPanel({
      extractionStatus: 'succeeded',
      candidates: [makeCandidate(), makeCandidate({ candidate_id: 'c2', ordinal: 1, statement: '第二条' })],
    });
    expect(screen.getByText(/候选结论 \(2 条\)/)).toBeInTheDocument();
  });

  it('shows saved tag for saved candidates', () => {
    renderPanel({
      extractionStatus: 'succeeded',
      candidates: [makeCandidate({ status: 'saved' })],
    });
    expect(screen.getByText('已保存')).toBeInTheDocument();
  });

  it('shows rejected tag for rejected candidates', () => {
    renderPanel({
      extractionStatus: 'succeeded',
      candidates: [makeCandidate({ status: 'rejected' })],
    });
    expect(screen.getByText('已拒绝')).toBeInTheDocument();
  });

  it('disables checkbox for saved candidates', () => {
    renderPanel({
      extractionStatus: 'succeeded',
      candidates: [makeCandidate({ status: 'saved' })],
    });
    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).toBeDisabled();
  });

  it('enables save button when candidate is selected', async () => {
    const onSave = vi.fn();
    renderPanel({
      extractionStatus: 'succeeded',
      candidates: [makeCandidate({ candidate_id: 'c1' })],
      onSave,
    });
    const checkbox = screen.getByRole('checkbox');
    await userEvent.click(checkbox);
    const saveButton = screen.getByRole('button', { name: /保存选中/ });
    expect(saveButton).toBeInTheDocument();
    await userEvent.click(saveButton);
    expect(onSave).toHaveBeenCalledWith([{ candidate_id: 'c1' }]);
  });
});
