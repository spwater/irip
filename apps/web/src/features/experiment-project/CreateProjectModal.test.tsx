import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { apiListDepartments } from '@/api/departments';
import { apiListUsers } from '@/api/governance';
import { CreateProjectModal } from './CreateProjectModal';

vi.mock('@/api/experiment-projects', () => ({
  apiCreateExperimentProject: vi.fn(),
}));

vi.mock('@/api/departments', () => ({
  apiListDepartments: vi.fn(),
}));

vi.mock('@/api/governance', () => ({
  apiListUsers: vi.fn(),
}));

vi.mock('@/shared/buildDeptTree', () => ({
  buildDeptTree: vi.fn((items) =>
    items.map((i: { id: string; display_name: string }) => ({
      value: i.id,
      title: i.display_name,
      selectable: true,
    })),
  ),
}));

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>{ui}</AntApp>
    </QueryClientProvider>,
  );
}

describe('CreateProjectModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListDepartments).mockResolvedValue({
      items: [
        { id: 'dept-001', code: 'lab-1', display_name: '材料实验室', description: null, status: 'active', sort_order: 0, parent_id: null, member_count: 5, children_count: 0, equipment_count: 2 },
      ],
      next_cursor: null,
      has_more: false,
    });
    vi.mocked(apiListUsers).mockResolvedValue({
      items: [
        { id: 'user-001', email: 'zhang@irip.com', display_name: '张三', roles: ['researcher'], status: 'active', department_id: 'dept-001', created_at: '', updated_at: '' },
      ],
      next_cursor: null,
      has_more: false,
    });
  });

  it('renders modal title 新建项目 when open', () => {
    renderWithClient(<CreateProjectModal open={true} onClose={vi.fn()} />);
    expect(screen.getByText('新建项目')).toBeInTheDocument();
  });

  it('renders form labels 所属单位 and 负责人 and 项目名称', () => {
    renderWithClient(<CreateProjectModal open={true} onClose={vi.fn()} />);
    expect(screen.getByText('所属单位')).toBeInTheDocument();
    expect(screen.getByText('负责人')).toBeInTheDocument();
    expect(screen.getByText('项目名称')).toBeInTheDocument();
  });

  it('renders project description field', () => {
    renderWithClient(<CreateProjectModal open={true} onClose={vi.fn()} />);
    expect(screen.getByText('项目描述')).toBeInTheDocument();
  });

  it('shows 创建 and 取消 buttons', () => {
    renderWithClient(<CreateProjectModal open={true} onClose={vi.fn()} />);
    expect(screen.getByRole('button', { name: /创\s*建/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /取\s*消/ })).toBeInTheDocument();
  });

  it('shows validation error for 项目名称 when creating empty', async () => {
    renderWithClient(<CreateProjectModal open={true} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: /创\s*建/ }));
    expect(await screen.findByText('请输入项目名称')).toBeInTheDocument();
  });

  it('calls onClose when 取消 clicked', async () => {
    const onClose = vi.fn();
    renderWithClient(<CreateProjectModal open={true} onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: /取\s*消/ }));
    expect(onClose).toHaveBeenCalled();
  });

  it('shows 可见单位 field with tooltip', () => {
    renderWithClient(<CreateProjectModal open={true} onClose={vi.fn()} />);
    expect(screen.getByText('可见单位')).toBeInTheDocument();
  });

  it('does not render modal content when closed', () => {
    renderWithClient(<CreateProjectModal open={false} onClose={vi.fn()} />);
    expect(screen.queryByText('新建项目')).not.toBeInTheDocument();
  });
});
