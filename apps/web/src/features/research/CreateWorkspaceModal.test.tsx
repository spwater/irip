import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { apiCreateWorkspace } from '@/api/research';
import { CreateWorkspaceModal } from './CreateWorkspaceModal';

vi.mock('@/api/research', () => ({
  apiCreateWorkspace: vi.fn(),
}));

function renderModal(props: {
  open?: boolean;
  onClose?: () => void;
  onCreated?: () => void;
}): void {
  render(
    <AntApp>
      <CreateWorkspaceModal
        open={props.open ?? true}
        onClose={props.onClose ?? vi.fn()}
        onCreated={props.onCreated ?? vi.fn()}
      />
    </AntApp>,
  );
}

describe('CreateWorkspaceModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders modal with title when open', () => {
    renderModal({ open: true });
    expect(screen.getByText('新建研究工作空间')).toBeInTheDocument();
  });

  it('renders name form field but not question field', () => {
    renderModal({ open: true });
    expect(screen.getByText('工作空间名称')).toBeInTheDocument();
    expect(screen.queryByText('主研究问题')).not.toBeInTheDocument();
  });

  it('shows validation error when submitting empty form', async () => {
    renderModal({ open: true });
    const okButton = screen.getByRole('button', { name: /创\s*建/ });
    await userEvent.click(okButton);
    expect(await screen.findByText('请输入名称')).toBeInTheDocument();
  });

  it('calls apiCreateWorkspace with name only on submit', async () => {
    vi.mocked(apiCreateWorkspace).mockResolvedValueOnce({
      workspace_id: 'ws-001',
      name: '测试工作空间',
      status: 'draft',
      latest_snapshot_number: null,
      turn_count: 0,
      active_run_status: null,
    });
    const onCreated = vi.fn();
    renderModal({ onCreated });

    const nameInput = screen.getByPlaceholderText('如：Na2O 含量对烧结性能的影响研究');
    await userEvent.type(nameInput, '测试工作空间');

    const okButton = screen.getByRole('button', { name: /创\s*建/ });
    await userEvent.click(okButton);

    expect(await vi.waitFor(() => vi.mocked(apiCreateWorkspace))).toHaveBeenCalledWith({
      name: '测试工作空间',
    });
    expect(onCreated).toHaveBeenCalled();
  });

  it('calls onClose when cancel button clicked', async () => {
    const onClose = vi.fn();
    renderModal({ onClose });
    const cancelButton = screen.getByRole('button', { name: /取\s*消/ });
    await userEvent.click(cancelButton);
    expect(onClose).toHaveBeenCalled();
  });
});
