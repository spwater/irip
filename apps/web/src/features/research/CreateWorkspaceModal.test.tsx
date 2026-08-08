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

  it('renders name and question form fields', () => {
    renderModal({ open: true });
    expect(screen.getByText('工作空间名称')).toBeInTheDocument();
    expect(screen.getByText('主研究问题')).toBeInTheDocument();
  });

  it('shows validation error when submitting empty form', async () => {
    renderModal({ open: true });
    const okButton = screen.getByRole('button', { name: /创\s*建/ });
    await userEvent.click(okButton);
    expect(await screen.findByText('请输入名称')).toBeInTheDocument();
  });

  it('calls apiCreateWorkspace with form values on submit', async () => {
    vi.mocked(apiCreateWorkspace).mockResolvedValueOnce({
      workspace_id: 'ws-001',
      name: '测试工作空间',
      status: 'draft',
      current_question_version: 0,
    });
    const onCreated = vi.fn();
    renderModal({ onCreated });

    const nameInput = screen.getByPlaceholderText('如：Na2O 含量对烧结性能的影响研究');
    await userEvent.type(nameInput, '测试工作空间');

    const questionInput = screen.getByPlaceholderText('如：不同 Na2O 含量对烧结矿冶金性能有何影响？');
    await userEvent.type(questionInput, '测试研究问题');

    const okButton = screen.getByRole('button', { name: /创\s*建/ });
    await userEvent.click(okButton);

    expect(await vi.waitFor(() => vi.mocked(apiCreateWorkspace))).toHaveBeenCalledWith({
      name: '测试工作空间',
      question_text: '测试研究问题',
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
