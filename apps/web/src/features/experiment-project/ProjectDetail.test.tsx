import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  apiGetExperimentProject,
  type ExperimentProjectDetailResponse,
} from '@/api/experiment-projects';
import { apiListDepartments } from '@/api/departments';
import { apiListUsers } from '@/api/governance';
import { ProjectDetail } from './ProjectDetail';

vi.mock('@/api/experiment-projects', () => ({
  apiGetExperimentProject: vi.fn(),
  apiUpdateExperimentProject: vi.fn(),
  apiUpdateExperimentProjectStatus: vi.fn(),
  apiDeleteExperimentProject: vi.fn(),
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

vi.mock('@/features/components/FlowDetail', () => ({
  FlowDetail: ({ projectId }: { projectId: string }) => (
    <div data-testid="flow-detail">FlowDetail {projectId}</div>
  ),
}));

const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}));

const activeProject: ExperimentProjectDetailResponse = {
  id: 'proj-001',
  department_id: 'dept-001',
  code: 'PRJ-001',
  display_name: '水泥组分研究',
  description: '研究水泥不同组分的影响',
  status: 'active',
  visible_departments: [],
  visibility_scope: 'tree',
  owner_user_id: 'user-001',
  owner_display_name: '张三',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  lock_version: 1,
  task_count: 5,
  fact_count: 12,
};

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>{ui}</AntApp>
    </QueryClientProvider>,
  );
}

describe('ProjectDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGetExperimentProject).mockResolvedValue(activeProject);
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

  it('renders 返回项目列表 button', async () => {
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    expect(await screen.findByText('返回项目列表')).toBeInTheDocument();
  });

  it('renders project name and code', async () => {
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    expect(await screen.findByText('水泥组分研究')).toBeInTheDocument();
    expect(screen.getByText('(PRJ-001)')).toBeInTheDocument();
  });

  it('renders 活跃 status tag for active project', async () => {
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    expect(await screen.findByText('活跃')).toBeInTheDocument();
  });

  it('renders task and fact counts', async () => {
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    expect(await screen.findByText(/任务数: 5/)).toBeInTheDocument();
    expect(screen.getByText(/数据数: 12/)).toBeInTheDocument();
  });

  it('renders department name and owner', async () => {
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    expect(await screen.findByText(/所属单位: 材料实验室/)).toBeInTheDocument();
    expect(screen.getByText(/负责人: 张三/)).toBeInTheDocument();
  });

  it('renders embedded FlowDetail with projectId', async () => {
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    expect(await screen.findByTestId('flow-detail')).toHaveTextContent('proj-001');
  });

  it('renders 编辑 and 归档 buttons for active project', async () => {
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    expect(await screen.findByText(/编\s*辑/)).toBeInTheDocument();
    expect(screen.getByText(/归\s*档/)).toBeInTheDocument();
  });

  it('navigates back when 返回项目列表 clicked', async () => {
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    await userEvent.click(await screen.findByText('返回项目列表'));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/lab-ops', search: { tab: 'flows' } });
  });

  it('opens edit modal when 编辑 clicked', async () => {
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    await screen.findByText('水泥组分研究');
    await userEvent.click(screen.getByText(/编\s*辑/));
    await waitFor(() => {
      expect(screen.getByText('编辑项目')).toBeInTheDocument();
    });
  });

  it('renders 恢复 and 删除 buttons for archived project', async () => {
    const archivedProject = { ...activeProject, status: 'archived' };
    vi.mocked(apiGetExperimentProject).mockResolvedValueOnce(archivedProject);
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    expect(await screen.findByText(/恢\s*复/)).toBeInTheDocument();
    expect(screen.getByText(/删\s*除/)).toBeInTheDocument();
  });

  it('renders 归档 status tag for archived project', async () => {
    const archivedProject = { ...activeProject, status: 'archived' };
    vi.mocked(apiGetExperimentProject).mockResolvedValueOnce(archivedProject);
    renderWithClient(<ProjectDetail projectId="proj-001" />);
    expect(await screen.findByText('归档')).toBeInTheDocument();
  });
});
