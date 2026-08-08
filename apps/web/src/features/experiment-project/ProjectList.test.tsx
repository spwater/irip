import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { apiListExperimentProjects, type ExperimentProjectListItem } from '@/api/experiment-projects';
import { apiListDepartments } from '@/api/departments';
import { ProjectList } from './ProjectList';

vi.mock('@/api/experiment-projects', () => ({
  apiListExperimentProjects: vi.fn(),
}));

vi.mock('@/api/departments', () => ({
  apiListDepartments: vi.fn(),
}));

vi.mock('./CreateProjectModal', () => ({
  CreateProjectModal: ({ open }: { open: boolean }) =>
    open ? <div data-testid="create-project-modal">CreateProjectModal</div> : null,
}));

const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}));

const activeProject: ExperimentProjectListItem = {
  id: 'proj-001',
  code: 'PRJ-001',
  display_name: '水泥组分研究',
  description: '研究水泥不同组分的影响',
  department_id: 'dept-001',
  department_name: '材料实验室',
  visible_departments: [],
  status: 'active',
  task_count: 5,
  owner_display_name: '张三',
  fact_count: 12,
  created_at: '2024-01-01T00:00:00Z',
};

const archivedProject: ExperimentProjectListItem = {
  id: 'proj-002',
  code: 'PRJ-002',
  display_name: '烧结性能研究',
  description: null,
  department_id: 'dept-001',
  department_name: '材料实验室',
  visible_departments: [],
  status: 'archived',
  task_count: 3,
  owner_display_name: null,
  fact_count: 8,
  created_at: '2024-01-01T00:00:00Z',
};

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>{ui}</AntApp>
    </QueryClientProvider>,
  );
}

describe('ProjectList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListExperimentProjects).mockResolvedValue({
      items: [activeProject],
      next_cursor: null,
      has_more: false,
    });
    vi.mocked(apiListDepartments).mockResolvedValue({
      items: [
        { id: 'dept-001', code: 'lab-1', display_name: '材料实验室', description: null, status: 'active', sort_order: 0, parent_id: null, member_count: 5, children_count: 0, equipment_count: 2 },
      ],
      next_cursor: null,
      has_more: false,
    });
  });

  it('renders 新建项目 button', () => {
    renderWithClient(<ProjectList />);
    expect(screen.getByRole('button', { name: /新建项目/ })).toBeInTheDocument();
  });

  it('renders 活跃 and 归档 radio buttons', () => {
    renderWithClient(<ProjectList />);
    expect(screen.getByText('活跃')).toBeInTheDocument();
    expect(screen.getByText('归档')).toBeInTheDocument();
  });

  it('shows active project card after loading', async () => {
    renderWithClient(<ProjectList />);
    expect(await screen.findByText('水泥组分研究')).toBeInTheDocument();
    expect(screen.getByText('5 个任务')).toBeInTheDocument();
    expect(screen.getByText('12 条数据')).toBeInTheDocument();
  });

  it('shows code on card', async () => {
    renderWithClient(<ProjectList />);
    expect(await screen.findByText(/code: PRJ-001/)).toBeInTheDocument();
  });

  it('shows department name and owner on card', async () => {
    renderWithClient(<ProjectList />);
    expect(await screen.findByText(/材料实验室 · 张三/)).toBeInTheDocument();
  });

  it('navigates to project detail when card clicked', async () => {
    renderWithClient(<ProjectList />);
    const card = await screen.findByText('水泥组分研究');
    await userEvent.click(card);
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/lab-ops',
      search: { tab: 'flows', project: 'proj-001' },
    });
  });

  it('opens CreateProjectModal when 新建项目 clicked', async () => {
    renderWithClient(<ProjectList />);
    await userEvent.click(screen.getByRole('button', { name: /新建项目/ }));
    expect(await screen.findByTestId('create-project-modal')).toBeInTheDocument();
  });

  it('switches to archived view and shows archived projects', async () => {
    vi.mocked(apiListExperimentProjects).mockImplementation((params) =>
      Promise.resolve(
        params?.status === 'archived'
          ? { items: [archivedProject], next_cursor: null, has_more: false }
          : { items: [], next_cursor: null, has_more: false },
      ),
    );
    renderWithClient(<ProjectList />);
    await userEvent.click(screen.getByText('归档'));
    await waitFor(() => {
      expect(apiListExperimentProjects).toHaveBeenCalledWith({ status: 'archived' });
    });
    expect(await screen.findByText('烧结性能研究')).toBeInTheDocument();
  });

  it('shows empty state when no active projects', async () => {
    vi.mocked(apiListExperimentProjects).mockResolvedValueOnce({
      items: [],
      next_cursor: null,
      has_more: false,
    });
    renderWithClient(<ProjectList />);
    expect(await screen.findByText('暂无活跃项目')).toBeInTheDocument();
  });

  it('shows empty state for archived when no archived projects', async () => {
    vi.mocked(apiListExperimentProjects).mockImplementation(() =>
      Promise.resolve({ items: [], next_cursor: null, has_more: false }),
    );
    renderWithClient(<ProjectList />);
    await userEvent.click(screen.getByText('归档'));
    expect(await screen.findByText('暂无归档项目')).toBeInTheDocument();
  });
});
