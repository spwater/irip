import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { apiListWorkspaces, type Workspace } from '@/api/research';
import { ResearchPage } from './ResearchPage';

vi.mock('@/api/research', () => ({
  apiListWorkspaces: vi.fn(),
}));

vi.mock('./WorkspaceCard', () => ({
  WorkspaceCard: ({ workspace, onClick }: { workspace: Workspace; onClick: () => void }) => (
    <div data-testid={`ws-card-${workspace.workspace_id}`} onClick={onClick}>
      {workspace.name}
    </div>
  ),
}));

vi.mock('./CreateWorkspaceModal', () => ({
  CreateWorkspaceModal: ({ open }: { open: boolean }) =>
    open ? <div data-testid="create-modal">CreateWorkspaceModal</div> : null,
}));

vi.mock('./WorkspaceDetail', () => ({
  WorkspaceDetail: ({ workspaceId, onBack }: { workspaceId: string; onBack: () => void }) => (
    <div>
      <div data-testid="ws-detail">{workspaceId}</div>
      <button onClick={onBack}>返回列表</button>
    </div>
  ),
}));

const mockWorkspaces: Workspace[] = [
  { workspace_id: 'ws-1', name: '烧结性能研究', status: 'draft', current_question_version: 1 },
  { workspace_id: 'ws-2', name: '熔炼工艺优化', status: 'archived', current_question_version: 2 },
];

function renderPage(): void {
  render(
    <AntApp>
      <ResearchPage />
    </AntApp>,
  );
}

describe('ResearchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListWorkspaces).mockResolvedValue({ items: mockWorkspaces, next_cursor: null });
  });

  it('renders 新建 Workspace button', () => {
    renderPage();
    expect(screen.getByRole('button', { name: /新建\s*Workspace/ })).toBeInTheDocument();
  });

  it('renders status filter segmented control with 全部/活跃/归档', () => {
    renderPage();
    expect(screen.getByText('全部')).toBeInTheDocument();
    expect(screen.getByText('活跃')).toBeInTheDocument();
    expect(screen.getByText('归档')).toBeInTheDocument();
  });

  it('renders workspace cards after loading', async () => {
    renderPage();
    expect(await screen.findByTestId('ws-card-ws-1')).toBeInTheDocument();
    expect(screen.getByTestId('ws-card-ws-2')).toBeInTheDocument();
  });

  it('filters workspaces by search text', async () => {
    renderPage();
    await screen.findByTestId('ws-card-ws-1');
    const searchInput = screen.getByPlaceholderText('搜索工作空间名称');
    await userEvent.type(searchInput, '烧结');
    await waitFor(() => {
      expect(screen.getByTestId('ws-card-ws-1')).toBeInTheDocument();
      expect(screen.queryByTestId('ws-card-ws-2')).not.toBeInTheDocument();
    });
  });

  it('opens create modal when 新建 Workspace clicked', async () => {
    renderPage();
    const createBtn = screen.getByRole('button', { name: /新建\s*Workspace/ });
    await userEvent.click(createBtn);
    expect(screen.getByTestId('create-modal')).toBeInTheDocument();
  });

  it('shows empty state when no workspaces returned', async () => {
    vi.mocked(apiListWorkspaces).mockResolvedValueOnce({ items: [], next_cursor: null });
    renderPage();
    expect(await screen.findByText('暂无研究工作空间，点击「新建 Workspace」开始')).toBeInTheDocument();
  });

  it('shows error message when API fails', async () => {
    vi.mocked(apiListWorkspaces).mockRejectedValueOnce(new Error('网络错误'));
    renderPage();
    expect(await screen.findByText(/加载失败/)).toBeInTheDocument();
  });

  it('switches to workspace detail when card clicked', async () => {
    renderPage();
    const card = await screen.findByTestId('ws-card-ws-1');
    await userEvent.click(card);
    expect(screen.getByTestId('ws-detail')).toHaveTextContent('ws-1');
  });

  it('returns to list when back button clicked in detail view', async () => {
    renderPage();
    const card = await screen.findByTestId('ws-card-ws-1');
    await userEvent.click(card);
    const backButton = screen.getByText('返回列表');
    await userEvent.click(backButton);
    expect(screen.getByTestId('ws-card-ws-1')).toBeInTheDocument();
  });
});
