import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { apiGetQueueStatus, apiCancelRun, type QueueStatus as QueueStatusType } from '@/api/research';
import { QueueStatus } from './QueueStatus';

vi.mock('@/api/research', () => ({
  apiGetQueueStatus: vi.fn(),
  apiCancelRun: vi.fn(),
}));

const mockQueueInfo: QueueStatusType = {
  position: 3,
  ahead_count: 2,
  estimated_wait_seconds: 480,
};

function renderQueueStatus(props: {
  workspaceId?: string;
  runId?: string;
  onCancel?: () => void;
  onCancelLoading?: boolean;
}): void {
  render(
    <AntApp>
      <QueueStatus
        workspaceId={props.workspaceId ?? 'ws-001'}
        runId={props.runId ?? 'run-001'}
        onCancel={props.onCancel ?? vi.fn()}
        onCancelLoading={props.onCancelLoading}
      />
    </AntApp>,
  );
}

describe('QueueStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGetQueueStatus).mockResolvedValue(mockQueueInfo);
    vi.mocked(apiCancelRun).mockResolvedValue(undefined);
  });

  it('renders queue position text', async () => {
    renderQueueStatus({});
    expect(await screen.findByText(/第\s*3\s*位/)).toBeInTheDocument();
  });

  it('renders ahead count', async () => {
    renderQueueStatus({});
    expect(await screen.findByText(/前方\s*2\s*位用户/)).toBeInTheDocument();
  });

  it('renders estimated wait time', async () => {
    renderQueueStatus({});
    expect(await screen.findByText(/预计等待\s*~8\s*分钟/)).toBeInTheDocument();
  });

  it('renders 取消排队 button', async () => {
    renderQueueStatus({});
    expect(await screen.findByRole('button', { name: /取消排队/ })).toBeInTheDocument();
  });

  it('calls apiCancelRun and onCancel when cancel clicked', async () => {
    const onCancel = vi.fn();
    renderQueueStatus({ onCancel });
    const cancelBtn = await screen.findByRole('button', { name: /取消排队/ });
    await userEvent.click(cancelBtn);
    await waitFor(() => {
      expect(vi.mocked(apiCancelRun)).toHaveBeenCalledWith('ws-001', 'run-001');
      expect(onCancel).toHaveBeenCalled();
    });
  });

  it('shows loading spinner initially', () => {
    // Don't resolve the mock immediately
    vi.mocked(apiGetQueueStatus).mockReturnValueOnce(new Promise(() => {}));
    renderQueueStatus({});
    // Spin component renders with tip text
    const spinner = document.querySelector('.ant-spin');
    expect(spinner).toBeTruthy();
  });
});
