import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { RunProgressPanel } from './RunProgressPanel';
import type { StepProgress, CoverageDeclaration } from '@/api/research';

const mockSteps: StepProgress[] = [
  { step_id: 's1', step_key: '数据加载', step_index: 0, status: 'succeeded', method: 'python', analysis_mode: 'full_compute', coverage_rate: 1.0, llm_read_rate: 0, is_sampled: false, attempt_count: 1, error_message: null },
  { step_id: 's2', step_key: 'LLM 分析', step_index: 1, status: 'running', method: 'llm', analysis_mode: 'retrieval', coverage_rate: 0.75, llm_read_rate: 0.75, is_sampled: false, attempt_count: 1, error_message: null },
  { step_id: 's3', step_key: '结果汇总', step_index: 2, status: 'pending', method: 'mixed', analysis_mode: null, coverage_rate: null, llm_read_rate: null, is_sampled: false, attempt_count: 0, error_message: null },
];

const mockCoverage: CoverageDeclaration = {
  analysis_mode: 'mixed',
  data_coverage_rate: 1.0,
  llm_read_rate: 0.75,
  is_sampled: false,
  batch_count: null,
  batch_progress: null,
  mode_reason: 'auto',
};

function renderPanel(props: {
  runStatus?: string;
  steps?: StepProgress[];
  coverageDeclaration?: CoverageDeclaration | null;
  startedAt?: string | null;
  completedAt?: string | null;
  onCancel?: () => void;
  onCancelLoading?: boolean;
}): void {
  render(
    <AntApp>
      <RunProgressPanel
        runId="run-001"
        runStatus={props.runStatus ?? 'running'}
        steps={props.steps ?? mockSteps}
        coverageDeclaration={props.coverageDeclaration ?? mockCoverage}
        startedAt={props.startedAt ?? '2025-01-01T00:00:00Z'}
        completedAt={props.completedAt ?? null}
        onCancel={props.onCancel ?? vi.fn()}
        onCancelLoading={props.onCancelLoading}
      />
    </AntApp>,
  );
}

describe('RunProgressPanel', () => {
  it('renders run status tag', () => {
    renderPanel({ runStatus: 'running' });
    expect(screen.getByText('运行中')).toBeInTheDocument();
  });

  it('renders progress text with completed/total steps', () => {
    renderPanel({});
    expect(screen.getByText('1 / 3 步')).toBeInTheDocument();
  });

  it('renders step keys in the list', () => {
    renderPanel({});
    expect(screen.getByText('数据加载')).toBeInTheDocument();
    expect(screen.getByText('LLM 分析')).toBeInTheDocument();
    expect(screen.getByText('结果汇总')).toBeInTheDocument();
  });

  it('renders method labels next to step keys', () => {
    renderPanel({});
    // The method label includes mode: "Python · 全量计算"
    expect(screen.getByText(/Python.*全量计算/)).toBeInTheDocument();
    expect(screen.getByText(/LLM.*检索探索/)).toBeInTheDocument();
    expect(screen.getByText('混合')).toBeInTheDocument();
  });

  it('renders coverage declaration text', () => {
    renderPanel({});
    // Coverage declaration is a single string containing "数据覆盖率 100%"
    const allText = screen.getAllByText(/数据覆盖率\s*100%/);
    expect(allText.length).toBeGreaterThanOrEqual(1);
  });

  it('shows 取消运行 button when run is active', () => {
    renderPanel({ runStatus: 'running' });
    expect(screen.getByRole('button', { name: /取消运行/ })).toBeInTheDocument();
  });

  it('hides 取消运行 button when run is completed', () => {
    renderPanel({ runStatus: 'succeeded', completedAt: '2025-01-01T00:10:00Z' });
    expect(screen.queryByRole('button', { name: /取消运行/ })).not.toBeInTheDocument();
  });

  it('calls onCancel when cancel button clicked', async () => {
    const onCancel = vi.fn();
    renderPanel({ onCancel });
    const cancelBtn = screen.getByRole('button', { name: /取消运行/ });
    await userEvent.click(cancelBtn);
    expect(onCancel).toHaveBeenCalled();
  });

  it('renders error message for failed step', () => {
    const failedSteps: StepProgress[] = [
      { step_id: 's1', step_key: '失败步骤', step_index: 0, status: 'failed', method: 'python', analysis_mode: null, coverage_rate: null, llm_read_rate: null, is_sampled: false, attempt_count: 2, error_message: '连接超时' },
    ];
    renderPanel({ steps: failedSteps, runStatus: 'failed', completedAt: '2025-01-01T00:05:00Z' });
    expect(screen.getByText(/连接超时/)).toBeInTheDocument();
  });

  it('renders coverage rate per step when available', () => {
    renderPanel({});
    expect(screen.getByText(/数据覆盖率\s*75%/)).toBeInTheDocument();
  });
});
