/**
 * 可信执行前端测试（阶段 2）
 *
 * 测试范围：
 * 1. RunProgressPanel 渲染（进度条 + 步骤状态 + 覆盖声明）
 * 2. QueueStatus 渲染（排队位置 + 前方用户 + 取消按钮）
 * 3. PlanReviewCard 渲染（步骤摘要 + 确认按钮）
 * 4. Run 状态色板
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// vi.mock 必须在 import 之前（vitest 会自动提升）
vi.mock('@/api/research', () => {
  return {
    apiGetQueueStatus: vi.fn(async () => ({
      position: 3,
      ahead_count: 2,
      estimated_wait_seconds: 600,
    })),
    apiCancelRun: vi.fn(async () => {}),
  };
});

vi.mock('antd', async () => {
  const ActualAntd = await vi.importActual('antd');
  return { ...ActualAntd as Record<string, unknown> };
});

import { RunProgressPanel } from '@/features/research/RunProgressPanel';
import { QueueStatus } from '@/features/research/QueueStatus';
import { PlanReviewCard } from '@/features/research/PlanReviewCard';
import type { RunProgress, CoverageDeclaration, PlanDetail } from '@/api/research';

// ---------------------------------------------------------------------------
// 辅助数据
// ---------------------------------------------------------------------------

function makeSteps(overrides: Partial<RunProgress['steps'][number]>[] = []): RunProgress['steps'] {
  return overrides.map((o, i) => ({
    step_id: `step-${i + 1}`,
    step_key: o.step_key || `step_${i + 1}`,
    step_index: i,
    status: o.status || 'pending',
    method: o.method || 'python',
    analysis_mode: o.analysis_mode ?? null,
    coverage_rate: o.coverage_rate ?? null,
    llm_read_rate: o.llm_read_rate ?? null,
    is_sampled: o.is_sampled ?? false,
    attempt_count: o.attempt_count ?? 0,
    error_message: o.error_message ?? null,
  }));
}

function makeCoverageDeclaration(overrides: Partial<CoverageDeclaration> = {}): CoverageDeclaration {
  return {
    analysis_mode: 'mixed',
    data_coverage_rate: 1.0,
    llm_read_rate: 0.75,
    is_sampled: false,
    batch_count: null,
    batch_progress: null,
    mode_reason: '混合分析',
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// 1. RunProgressPanel 渲染
// ---------------------------------------------------------------------------

describe('RunProgressPanel', () => {
  it('renders progress bar with correct step count', () => {
    const steps = makeSteps([
      { step_key: 's1', status: 'succeeded' },
      { step_key: 's2', status: 'running' },
      { step_key: 's3', status: 'pending' },
    ]);

    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="running"
        steps={steps}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt={null}
        onCancel={() => {}}
      />,
    );

    // 进度条显示 1/3 步（只有 succeeded 计为完成）
    expect(screen.getByText(/1 \/ 3 步/)).toBeInTheDocument();
  });

  it('renders coverage declaration text', () => {
    const cd = makeCoverageDeclaration({
      analysis_mode: 'mixed',
      data_coverage_rate: 1.0,
      llm_read_rate: 0.75,
      is_sampled: false,
    });

    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="running"
        steps={makeSteps([{ status: 'succeeded' }])}
        coverageDeclaration={cd}
        startedAt="2026-01-01T00:00:00Z"
        completedAt={null}
        onCancel={() => {}}
      />,
    );

    // 覆盖声明包含模式名称和覆盖率
    expect(screen.getByText(/混合分析/)).toBeInTheDocument();
    expect(screen.getByText(/100%/)).toBeInTheDocument();
    expect(screen.getByText(/75%/)).toBeInTheDocument();
  });

  it('renders cancel button when run is active', () => {
    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="running"
        steps={makeSteps([{ status: 'running' }])}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt={null}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText('取消运行')).toBeInTheDocument();
  });

  it('does not render cancel button when run is completed', () => {
    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="succeeded"
        steps={makeSteps([{ status: 'succeeded' }])}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt="2026-01-01T01:00:00Z"
        onCancel={() => {}}
      />,
    );

    expect(screen.queryByText('取消运行')).not.toBeInTheDocument();
  });

  it('calls onCancel when cancel button is clicked', () => {
    const onCancel = vi.fn();
    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="running"
        steps={makeSteps([{ status: 'running' }])}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt={null}
        onCancel={onCancel}
      />,
    );

    fireEvent.click(screen.getByText('取消运行'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('renders step keys in the list', () => {
    const steps = makeSteps([
      { step_key: 'data_cleaning', status: 'succeeded' },
      { step_key: 'statistical_analysis', status: 'running' },
    ]);

    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="running"
        steps={steps}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt={null}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText('data_cleaning')).toBeInTheDocument();
    expect(screen.getByText('statistical_analysis')).toBeInTheDocument();
  });

  it('renders error message for failed steps', () => {
    const steps = makeSteps([
      { step_key: 'failed_step', status: 'failed', error_message: 'Syntax error in script' },
    ]);

    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="failed"
        steps={steps}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt="2026-01-01T01:00:00Z"
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText(/Syntax error in script/)).toBeInTheDocument();
  });

  it('renders batch progress when chunked mode has batches', () => {
    const cd = makeCoverageDeclaration({
      analysis_mode: 'chunked_full_scan',
      data_coverage_rate: 0.8,
      llm_read_rate: 0.8,
      batch_count: 5,
      batch_progress: 4,
    });

    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="running"
        steps={makeSteps([{ status: 'running' }])}
        coverageDeclaration={cd}
        startedAt="2026-01-01T00:00:00Z"
        completedAt={null}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText(/批次 4\/5/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 2. QueueStatus 渲染
// ---------------------------------------------------------------------------

describe('QueueStatus', () => {
  it('renders queue position and ahead count', async () => {
    render(
      <QueueStatus
        workspaceId="ws-001"
        runId="run-001"
        onCancel={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('正在排队等待执行')).toBeInTheDocument();
    });

    // 排队位置
    expect(screen.getByText(/第 3 位/)).toBeInTheDocument();
    // 前方用户数
    expect(screen.getByText(/前方 2 位用户/)).toBeInTheDocument();
  });

  it('renders estimated wait time', async () => {
    render(
      <QueueStatus
        workspaceId="ws-001"
        runId="run-001"
        onCancel={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/~10 分钟/)).toBeInTheDocument();
    });
  });

  it('renders cancel button', async () => {
    render(
      <QueueStatus
        workspaceId="ws-001"
        runId="run-001"
        onCancel={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('取消排队')).toBeInTheDocument();
    });
  });

  it('calls apiCancelRun when cancel button is clicked', async () => {
    const { apiCancelRun } = await import('@/api/research');
    const onCancel = vi.fn();

    render(
      <QueueStatus
        workspaceId="ws-001"
        runId="run-001"
        onCancel={onCancel}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('取消排队')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('取消排队'));

    await waitFor(() => {
      expect(apiCancelRun).toHaveBeenCalledWith('ws-001', 'run-001');
      expect(onCancel).toHaveBeenCalled();
    });
  });
});

// ---------------------------------------------------------------------------
// 3. PlanReviewCard 渲染
// ---------------------------------------------------------------------------

describe('PlanReviewCard', () => {
  function makePlan(overrides: Partial<PlanDetail> = {}): PlanDetail {
    return {
      plan_id: 'plan-001',
      workspace_id: 'ws-001',
      version_number: 1,
      status: 'draft',
      dag_structure: {
        steps: [
          {
            step_key: 'step_1',
            question: '数据完整性检查',
            evidence_refs: [],
            method: 'python',
            strategy: 'full',
            expected_output: '数据质量报告',
            risks: ['缺失值可能导致偏差'],
            dependencies: [],
            requires_full: true,
            per_record_semantic: false,
            cross_record_reasoning: false,
            allows_sampling: false,
            estimated_tokens: 50000,
            resource_tier: 'standard',
            analysis_mode: 'full_compute',
          },
          {
            step_key: 'step_2',
            question: '语义分析',
            evidence_refs: [],
            method: 'llm',
            strategy: 'chunked',
            expected_output: '语义分析报告',
            risks: [],
            dependencies: ['step_1'],
            requires_full: true,
            per_record_semantic: true,
            cross_record_reasoning: false,
            allows_sampling: false,
            estimated_tokens: 200000,
            resource_tier: 'standard',
            analysis_mode: 'chunked_full_scan',
          },
        ],
      },
      coverage_declaration: makeCoverageDeclaration(),
      created_at: '2026-01-01T00:00:00Z',
      confirmed_at: null,
      ...overrides,
    };
  }

  it('renders plan version and step count', () => {
    render(
      <PlanReviewCard
        plan={makePlan()}
        workspaceId="ws-001"
        onConfirm={() => {}}
        onAdjust={() => {}}
      />,
    );

    expect(screen.getByText(/分析计划 v1/)).toBeInTheDocument();
    expect(screen.getByText('2 步')).toBeInTheDocument();
  });

  it('renders step summaries', () => {
    render(
      <PlanReviewCard
        plan={makePlan()}
        workspaceId="ws-001"
        onConfirm={() => {}}
        onAdjust={() => {}}
      />,
    );

    // 步骤 key 显示
    expect(screen.getByText('step_1')).toBeInTheDocument();
    expect(screen.getByText('step_2')).toBeInTheDocument();
  });

  it('renders confirm and adjust buttons when draft', () => {
    render(
      <PlanReviewCard
        plan={makePlan()}
        workspaceId="ws-001"
        onConfirm={() => {}}
        onAdjust={() => {}}
      />,
    );

    expect(screen.getByText('确认计划')).toBeInTheDocument();
    expect(screen.getByText('调整计划')).toBeInTheDocument();
  });

  it('renders confirmed status bar when status is confirmed', () => {
    render(
      <PlanReviewCard
        plan={makePlan({ status: 'confirmed' })}
        workspaceId="ws-001"
        onConfirm={() => {}}
        onAdjust={() => {}}
      />,
    );

    // 已确认时显示"已确认计划"状态条
    expect(screen.getByText(/已确认计划 v1/)).toBeInTheDocument();
    // 不显示确认和调整按钮
    expect(screen.queryByText('确认计划')).not.toBeInTheDocument();
    expect(screen.queryByText('调整计划')).not.toBeInTheDocument();
  });

  it('calls onConfirm when confirm button is clicked', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <PlanReviewCard
        plan={makePlan()}
        workspaceId="ws-001"
        onConfirm={onConfirm}
        onAdjust={() => {}}
      />,
    );

    fireEvent.click(screen.getByText('确认计划'));

    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalledTimes(1);
    });
  });

  it('calls onAdjust when adjust button is clicked', () => {
    const onAdjust = vi.fn();
    render(
      <PlanReviewCard
        plan={makePlan()}
        workspaceId="ws-001"
        onConfirm={() => {}}
        onAdjust={onAdjust}
      />,
    );

    fireEvent.click(screen.getByText('调整计划'));
    expect(onAdjust).toHaveBeenCalledTimes(1);
  });

  it('renders coverage declaration preview', () => {
    render(
      <PlanReviewCard
        plan={makePlan()}
        workspaceId="ws-001"
        onConfirm={() => {}}
        onAdjust={() => {}}
      />,
    );

    // 覆盖声明预览
    expect(screen.getByText(/混合分析/)).toBeInTheDocument();
    expect(screen.getByText(/100%/)).toBeInTheDocument();
    expect(screen.getByText(/75%/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 4. Run 状态色板
// ---------------------------------------------------------------------------

describe('Run status colors', () => {
  it('renders queued status as gray tag', () => {
    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="queued"
        steps={makeSteps([{ status: 'pending' }])}
        coverageDeclaration={null}
        startedAt={null}
        completedAt={null}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText('排队中')).toBeInTheDocument();
  });

  it('renders running status with active label', () => {
    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="running"
        steps={makeSteps([{ status: 'running' }])}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt={null}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText('运行中')).toBeInTheDocument();
  });

  it('renders succeeded status with success label', () => {
    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="succeeded"
        steps={makeSteps([{ status: 'succeeded' }])}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt="2026-01-01T01:00:00Z"
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText('成功')).toBeInTheDocument();
  });

  it('renders failed status with failure label', () => {
    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="failed"
        steps={makeSteps([{ status: 'failed' }])}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt="2026-01-01T01:00:00Z"
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText('失败')).toBeInTheDocument();
  });

  it('renders partially_succeeded status with warning label', () => {
    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="partially_succeeded"
        steps={makeSteps([
          { status: 'succeeded' },
          { status: 'failed' },
        ])}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt="2026-01-01T01:00:00Z"
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText('部分成功')).toBeInTheDocument();
  });

  it('renders cancelled status with cancelled label', () => {
    render(
      <RunProgressPanel
        runId="run-001"
        runStatus="cancelled"
        steps={makeSteps([{ status: 'skipped' }])}
        coverageDeclaration={null}
        startedAt="2026-01-01T00:00:00Z"
        completedAt="2026-01-01T00:30:00Z"
        onCancel={() => {}}
      />,
    );

    expect(screen.getByText('已取消')).toBeInTheDocument();
  });
});
