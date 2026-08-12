import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { SynthesisComposer } from './SynthesisComposer';

vi.mock('@/api/researchTimeline', () => ({
  createSynthesisTurn: vi.fn(),
}));

import { createSynthesisTurn } from '@/api/researchTimeline';

function renderComposer(props: {
  workspaceId?: string;
  snapshotId?: string;
  selectedRevisionIds?: string[];
  onCreated?: (turnId: string) => void;
}): void {
  render(
    <AntApp>
      <SynthesisComposer
        workspaceId={props.workspaceId ?? 'ws-001'}
        snapshotId={props.snapshotId ?? 'snap-001'}
        selectedRevisionIds={props.selectedRevisionIds ?? []}
        onCreated={props.onCreated ?? vi.fn()}
      />
    </AntApp>,
  );
}

describe('SynthesisComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows count and min requirement', () => {
    renderComposer({ selectedRevisionIds: [] });
    expect(screen.getByText(/已选 0 条结论/)).toBeInTheDocument();
    expect(screen.getByText(/至少选择 2 条/)).toBeInTheDocument();
  });

  it('disables button when fewer than 2 selected', () => {
    renderComposer({ selectedRevisionIds: ['r1'] });
    const button = screen.getByRole('button', { name: '综合所选' });
    expect(button).toBeDisabled();
  });

  it('enables button when 2 selected', () => {
    renderComposer({ selectedRevisionIds: ['r1', 'r2'] });
    const button = screen.getByRole('button', { name: '综合所选' });
    expect(button).not.toBeDisabled();
  });

  it('shows warning when only 1 selected', () => {
    renderComposer({ selectedRevisionIds: ['r1'] });
    expect(screen.getByText(/至少选择 2 条结论才能进行综合分析/)).toBeInTheDocument();
  });

  it('calls createSynthesisTurn on submit', async () => {
    vi.mocked(createSynthesisTurn).mockResolvedValueOnce({
      turn_id: 'turn-001',
      workspace_id: 'ws-001',
      turn_number: 1,
      kind: 'synthesis',
      status: 'question_draft',
      question_text: '综合所选',
      question_origin: 'synthesis',
      evidence_snapshot_id: 'snap-001',
    });
    renderComposer({ selectedRevisionIds: ['r1', 'r2'] });
    await userEvent.click(screen.getByRole('button', { name: '综合所选' }));
    await waitFor(() => {
      expect(createSynthesisTurn).toHaveBeenCalledWith('ws-001', {
        evidence_snapshot_id: 'snap-001',
        selected_conclusion_revision_ids: ['r1', 'r2'],
      });
    });
  });

  it('shows error on failure', async () => {
    vi.mocked(createSynthesisTurn).mockRejectedValueOnce(new Error('Network error'));
    renderComposer({ selectedRevisionIds: ['r1', 'r2'] });
    await userEvent.click(screen.getByRole('button', { name: '综合所选' }));
    await waitFor(() => {
      expect(screen.getByText('综合失败')).toBeInTheDocument();
    });
  });
});
