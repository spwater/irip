import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { ConclusionLibrary } from './ConclusionLibrary';
import type { ConclusionRef } from '@/api/researchTimeline';

function makeConclusion(overrides: Partial<ConclusionRef & {
  source_turn_number: number | null;
  snapshot_number: number | null;
}> = {}): ConclusionRef & { source_turn_number: number | null; snapshot_number: number | null } {
  return {
    conclusion_id: 'c-001',
    workspace_id: 'ws-001',
    source_type: 'ai_original',
    evidence_status: 'data_supported',
    status: 'active',
    revision_number: 1,
    statement: '温度升高与收率上升相关',
    source_turn_number: null,
    snapshot_number: null,
    ...overrides,
  };
}

function renderLibrary(props: {
  conclusions?: ReturnType<typeof makeConclusion>[];
  selectedRevisionIds?: Set<string>;
  onToggle?: (id: string) => void;
  maxSelection?: number;
}): void {
  render(
    <AntApp>
      <ConclusionLibrary
        conclusions={props.conclusions ?? []}
        selectedRevisionIds={props.selectedRevisionIds ?? new Set()}
        onToggle={props.onToggle ?? vi.fn()}
        maxSelection={props.maxSelection ?? 20}
      />
    </AntApp>,
  );
}

describe('ConclusionLibrary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows empty state when no conclusions', () => {
    renderLibrary({});
    expect(screen.getByText('暂无已保存结论')).toBeInTheDocument();
  });

  it('renders conclusion statement', () => {
    renderLibrary({
      conclusions: [makeConclusion({ statement: '批次差异显著' })],
    });
    expect(screen.getByText('批次差异显著')).toBeInTheDocument();
  });

  it('shows AI badge for ai_original', () => {
    renderLibrary({
      conclusions: [makeConclusion({ source_type: 'ai_original' })],
    });
    expect(screen.getByText('AI')).toBeInTheDocument();
  });

  it('shows manual badge for manual', () => {
    renderLibrary({
      conclusions: [makeConclusion({ source_type: 'manual' })],
    });
    expect(screen.getByText('人工')).toBeInTheDocument();
  });

  it('shows unverified tag for manual_unverified', () => {
    renderLibrary({
      conclusions: [makeConclusion({ evidence_status: 'manual_unverified' })],
    });
    expect(screen.getByText('未验证')).toBeInTheDocument();
  });

  it('calls onToggle when checkbox clicked', async () => {
    const onToggle = vi.fn();
    renderLibrary({
      conclusions: [makeConclusion({ conclusion_id: 'c-1' })],
      onToggle,
    });
    const checkbox = screen.getByRole('checkbox');
    await userEvent.click(checkbox);
    expect(onToggle).toHaveBeenCalledWith('c-1');
  });

  it('shows selected count', () => {
    renderLibrary({
      conclusions: [makeConclusion({ conclusion_id: 'c-1' }), makeConclusion({ conclusion_id: 'c-2' })],
      selectedRevisionIds: new Set(['c-1']),
    });
    expect(screen.getByText(/已选 1\/20/)).toBeInTheDocument();
  });

  it('shows source turn number', () => {
    renderLibrary({
      conclusions: [makeConclusion({ source_turn_number: 3 })],
    });
    expect(screen.getByText(/轮次 #3/)).toBeInTheDocument();
  });

  it('shows snapshot version', () => {
    renderLibrary({
      conclusions: [makeConclusion({ snapshot_number: 2 })],
    });
    expect(screen.getByText(/快照 v2/)).toBeInTheDocument();
  });
});
