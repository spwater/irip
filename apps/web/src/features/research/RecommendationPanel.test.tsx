import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { RecommendationPanel } from './RecommendationPanel';

// Mock the API module
const mockGetActive = vi.fn();
const mockRetry = vi.fn();
vi.mock('../../api/researchTimeline', () => ({
  getActiveRecommendation: (...args: unknown[]) => mockGetActive(...args),
  retryRecommendation: (...args: unknown[]) => mockRetry(...args),
}));

function renderPanel(props: {
  workspaceId?: string;
  snapshotNumber?: number | null;
  refreshKey?: number;
  onAdopt?: (q: string, id: string | null) => void;
}): void {
  render(
    <AntApp>
      <RecommendationPanel
        workspaceId={props.workspaceId ?? 'ws-1'}
        snapshotNumber={props.snapshotNumber === undefined ? 1 : props.snapshotNumber}
        refreshKey={props.refreshKey ?? 0}
        onAdopt={props.onAdopt ?? vi.fn()}
      />
    </AntApp>,
  );
}

describe('RecommendationPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetActive.mockResolvedValue({
      batch_id: '',
      workspace_id: 'ws-1',
      status: 'none',
      items: [],
    });
    mockRetry.mockResolvedValue({
      batch_id: 'b1',
      workspace_id: 'ws-1',
      status: 'queued',
      item_count: 0,
    });
  });

  it('renders without crashing', () => {
    renderPanel({});
    expect(true).toBeTruthy();
  });

  it('shows prompt to freeze snapshot when no snapshot', () => {
    mockGetActive.mockResolvedValue({
      batch_id: '',
      workspace_id: 'ws-1',
      status: 'none',
      items: [],
    });
    renderPanel({ snapshotNumber: null });
    // snapshotNumber check is now before loading, so text appears immediately
    expect(screen.getByText(/请先冻结快照/)).toBeInTheDocument();
  });

  it('shows manual entry when no recommendations', async () => {
    mockGetActive.mockResolvedValue({
      batch_id: 'b1',
      workspace_id: 'ws-1',
      status: 'succeeded',
      items: [],
    });
    renderPanel({});
    expect(await screen.findByText(/没有推荐问题/)).toBeInTheDocument();
  });

  it('allows manual question entry', async () => {
    mockGetActive.mockResolvedValue({
      batch_id: 'b1',
      workspace_id: 'ws-1',
      status: 'succeeded',
      items: [],
    });
    const onAdopt = vi.fn();
    renderPanel({ onAdopt });
    const input = await screen.findByPlaceholderText(/输入研究问题/);
    await userEvent.type(input, '测试问题');
    const button = screen.getByRole('button', { name: '提交问题' });
    await userEvent.click(button);
    expect(onAdopt).toHaveBeenCalledWith('测试问题', null);
  });
});
