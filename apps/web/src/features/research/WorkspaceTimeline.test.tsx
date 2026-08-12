import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { App as AntApp } from 'antd';
import { WorkspaceTimeline } from './WorkspaceTimeline';
import type { TimelinePage } from '@/api/researchTimeline';

vi.mock('@/api/researchTimeline', () => ({
  listTimeline: vi.fn(),
}));

import { listTimeline } from '@/api/researchTimeline';
import type { TimelineItem } from '@/api/researchTimeline';

function mockPage(
  items: Partial<TimelineItem>[],
  nextCursor: string | null,
): TimelinePage {
  const defaults: TimelineItem = {
    turn_id: '',
    turn_number: 0,
    kind: 'analysis',
    status: 'question_draft',
    question_text: '',
    question_origin: 'manual',
    snapshot_number: 0,
    selected_conclusion_count: 0,
    has_result: false,
    has_candidates: false,
    created_at: '',
  };
  return {
    items: items.map((item) => ({ ...defaults, ...item }) as TimelineItem),
    next_cursor: nextCursor,
    active_run_status: null,
  };
}

function renderTimeline(props: { workspaceId?: string; onTurnClick?: (id: string) => void }): void {
  render(
    <AntApp>
      <WorkspaceTimeline
        workspaceId={props.workspaceId ?? 'ws-001'}
        onTurnClick={props.onTurnClick}
      />
    </AntApp>,
  );
}

describe('WorkspaceTimeline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading spinner initially', () => {
    vi.mocked(listTimeline).mockReturnValue(new Promise(() => {}));
    renderTimeline({});
    expect(document.querySelector('.ant-spin')).toBeTruthy();
  });

  it('shows empty state when no turns', async () => {
    vi.mocked(listTimeline).mockResolvedValueOnce(mockPage([], null));
    renderTimeline({});
    await waitFor(() => {
      expect(screen.getByText('暂无研究轮次')).toBeInTheDocument();
    });
  });

  it('renders turn cards', async () => {
    vi.mocked(listTimeline).mockResolvedValueOnce(
      mockPage([
        {
          turn_id: 't1',
          turn_number: 1,
          kind: 'analysis',
          status: 'succeeded',
          question_text: '温度影响?',
          question_origin: 'manual',
          snapshot_number: 1,
          selected_conclusion_count: 0,
          has_result: true,
          has_candidates: false,
          created_at: '2026-08-12T10:00:00Z',
        },
      ], null),
    );
    renderTimeline({});
    await waitFor(() => {
      expect(screen.getByText('#1 温度影响?')).toBeInTheDocument();
    });
  });

  it('shows load more button when next_cursor exists', async () => {
    vi.mocked(listTimeline).mockResolvedValueOnce(
      mockPage([
        {
          turn_id: 't1',
          turn_number: 1,
          kind: 'analysis',
          status: 'succeeded',
          question_text: 'Q1',
          question_origin: 'manual',
          snapshot_number: 1,
          selected_conclusion_count: 0,
          has_result: false,
          has_candidates: false,
          created_at: '2026-08-12T10:00:00Z',
        },
      ], 'cursor-1'),
    );
    renderTimeline({});
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '加载更多' })).toBeInTheDocument();
    });
  });

  it('does not show load more when next_cursor is null', async () => {
    vi.mocked(listTimeline).mockResolvedValueOnce(mockPage([], null));
    renderTimeline({});
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument();
    });
  });

  it('shows active run warning', async () => {
    vi.mocked(listTimeline).mockResolvedValueOnce({
      items: [{
        turn_id: 't1',
        turn_number: 1,
        kind: 'analysis',
        status: 'running',
        question_text: 'Q1',
        question_origin: 'manual',
        snapshot_number: 1,
        selected_conclusion_count: 0,
        has_result: false,
        has_candidates: false,
        created_at: '2026-08-12T10:00:00Z',
      }],
      next_cursor: null,
      active_run_status: 'running',
    });
    renderTimeline({});
    await waitFor(() => {
      expect(screen.getByText(/活跃分析任务/)).toBeInTheDocument();
    });
  });

  it('calls onTurnClick when card is clicked', async () => {
    const onTurnClick = vi.fn();
    vi.mocked(listTimeline).mockResolvedValueOnce(
      mockPage([
        {
          turn_id: 't1',
          turn_number: 1,
          kind: 'analysis',
          status: 'succeeded',
          question_text: 'Q1',
          question_origin: 'manual',
          snapshot_number: 1,
          selected_conclusion_count: 0,
          has_result: false,
          has_candidates: false,
          created_at: '2026-08-12T10:00:00Z',
        },
      ], null),
    );
    renderTimeline({ onTurnClick });
    await waitFor(() => {
      expect(screen.getByText('#1 Q1')).toBeInTheDocument();
    });
    screen.getByText('#1 Q1').click();
    expect(onTurnClick).toHaveBeenCalledWith('t1');
  });
});
