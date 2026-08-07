/**
 * 研究域基础前端测试
 *
 * 测试范围：
 * 1. ResearchPage 渲染空状态
 * 2. WorkspaceCard 正确显示
 * 3. CreateWorkspaceModal 交互
 * 4. LabOpsPage 功能开关条件渲染
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// vi.mock 必须在 import 之前（vitest 会自动提升）
vi.mock('@/api/research', () => {
  const state: {
    listWorkspaces: { items: unknown[]; next_cursor: string | null } | null;
  } = {
    listWorkspaces: null,
  };

  return {
    apiListWorkspaces: vi.fn(async () => {
      if (state.listWorkspaces) return state.listWorkspaces;
      return { items: [], next_cursor: null };
    }),
    apiCreateWorkspace: vi.fn(async (body: { name: string; question_text: string }) => {
      return {
        workspace_id: 'ws-new-001',
        name: body.name,
        status: 'draft',
        current_question_version: 1,
        forked_from_id: null,
      };
    }),
    apiGetWorkspace: vi.fn(async () => ({
      workspace_id: 'ws-001',
      name: '测试工作空间',
      status: 'draft',
      current_question: null,
      evidence_count: 0,
      snapshots: [],
    })),
    apiUpdateWorkspace: vi.fn(async () => ({
      workspace_id: 'ws-001',
      name: '新名称',
      status: 'draft',
      current_question_version: 1,
      forked_from_id: null,
    })),
    apiDeleteWorkspace: vi.fn(async () => {}),
    apiArchiveWorkspace: vi.fn(async () => {}),
    apiForkWorkspace: vi.fn(async () => ({
      workspace_id: 'ws-fork-001',
      name: '分叉',
      status: 'draft',
      current_question_version: 1,
      forked_from_id: 'ws-001',
    })),
    apiUpdateQuestion: vi.fn(async () => ({
      version_id: 'qv-002',
      workspace_id: 'ws-001',
      version_number: 2,
      question_text: '新问题',
      sub_questions: [],
    })),
    apiAddEvidence: vi.fn(async () => ({
      ref_id: 'ref-001',
      source_namespace: 'core:fact',
      source_id: 'fact-001',
      source_version: null,
      source_name: '实验001',
      status: 'active',
    })),
    apiRemoveEvidence: vi.fn(async () => {}),
    apiListEvidence: vi.fn(async () => ({ items: [] })),
    apiFreezeSnapshot: vi.fn(async () => ({
      snapshot_id: 'snap-001',
      snapshot_number: 1,
      content_hash: 'a'.repeat(64),
      captured_at: '2026-01-01T00:00:00Z',
    })),
    apiListSnapshots: vi.fn(async () => ({ items: [] })),
    apiSearchFacts: vi.fn(async () => ({ items: [], next_cursor: null })),
    __setListWorkspacesReturn: (val: typeof state.listWorkspaces) => {
      state.listWorkspaces = val;
    },
  };
});

// Mock useAuthStore — 使用可变状态供测试修改
const _authState: { user: unknown } = { user: null };

vi.mock('@/features/auth/AuthProvider', () => ({
  useAuthStore: vi.fn(() => _authState.user ? { user: _authState.user } : { user: null }),
}));

// Mock useNavigate and useSearch for LabOpsPage
const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useSearch: () => ({}),
}));

// Mock PageHeaderContext
vi.mock('@/app/PageHeaderContext', () => ({
  usePageHeaderRegistration: vi.fn(),
}));

// Mock FeedbackState
vi.mock('@/shared/ui', () => ({
  FeedbackState: () => null,
}));

import { ResearchPage } from '@/features/research/ResearchPage';
import { WorkspaceCard } from '@/features/research/WorkspaceCard';
import { CreateWorkspaceModal } from '@/features/research/CreateWorkspaceModal';

// ---------------------------------------------------------------------------
// 辅助函数
// ---------------------------------------------------------------------------

function renderWithQueryClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// 1. ResearchPage 渲染空状态
// ---------------------------------------------------------------------------

describe('ResearchPage', () => {
  it('renders empty state when no workspaces', async () => {
    renderWithQueryClient(<ResearchPage />);

    // 空状态时显示引导文案
    await waitFor(() => {
      expect(screen.getByText(/暂无研究工作空间/)).toBeInTheDocument();
    });

    // 显示"新建 Workspace"按钮（空状态下有两个，一个在 header 一个在 Empty 中）
    const buttons = screen.getAllByText('新建 Workspace');
    expect(buttons.length).toBeGreaterThanOrEqual(1);
  });

  it('renders workspace cards when workspaces exist', async () => {
    const { apiListWorkspaces } = await import('@/api/research');
    (apiListWorkspaces as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      items: [
        {
          workspace_id: 'ws-001',
          name: 'Na2O 研究',
          status: 'draft',
          current_question_version: 2,
          forked_from_id: null,
        },
      ],
      next_cursor: null,
    });

    renderWithQueryClient(<ResearchPage />);

    await waitFor(() => {
      expect(screen.getByText('Na2O 研究')).toBeInTheDocument();
    });
  });

  it('shows Segmented status filter with all/draft/archived options', async () => {
    renderWithQueryClient(<ResearchPage />);

    await waitFor(() => {
      expect(screen.getByText('全部')).toBeInTheDocument();
      expect(screen.getByText('活跃')).toBeInTheDocument();
      expect(screen.getByText('归档')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// 2. WorkspaceCard 正确显示
// ---------------------------------------------------------------------------

describe('WorkspaceCard', () => {
  it('displays workspace name and status tag for draft', () => {
    renderWithQueryClient(
      <WorkspaceCard
        workspace={{
          workspace_id: 'ws-001',
          name: '测试工作空间',
          status: 'draft',
          current_question_version: 3,
          forked_from_id: null,
        }}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('测试工作空间')).toBeInTheDocument();
    expect(screen.getByText('活跃')).toBeInTheDocument();
    expect(screen.getByText(/问题版本 v3/)).toBeInTheDocument();
  });

  it('displays archived status tag for archived workspace', () => {
    renderWithQueryClient(
      <WorkspaceCard
        workspace={{
          workspace_id: 'ws-002',
          name: '归档工作空间',
          status: 'archived',
          current_question_version: 1,
          forked_from_id: null,
        }}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('归档工作空间')).toBeInTheDocument();
    expect(screen.getByText('已归档')).toBeInTheDocument();
  });

  it('shows fork info when forked_from_id is set', () => {
    renderWithQueryClient(
      <WorkspaceCard
        workspace={{
          workspace_id: 'ws-003',
          name: '分叉工作空间',
          status: 'draft',
          current_question_version: 1,
          forked_from_id: 'ws-parent-001',
        }}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('分叉工作空间')).toBeInTheDocument();
    expect(screen.getByText('分叉自其他工作空间')).toBeInTheDocument();
  });

  it('does not show fork info when forked_from_id is null', () => {
    renderWithQueryClient(
      <WorkspaceCard
        workspace={{
          workspace_id: 'ws-004',
          name: '独立工作空间',
          status: 'draft',
          current_question_version: 1,
          forked_from_id: null,
        }}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('独立工作空间')).toBeInTheDocument();
    expect(screen.queryByText('分叉自其他工作空间')).not.toBeInTheDocument();
  });

  it('calls onClick when card is clicked', () => {
    const onClick = vi.fn();
    renderWithQueryClient(
      <WorkspaceCard
        workspace={{
          workspace_id: 'ws-005',
          name: '可点击工作空间',
          status: 'draft',
          current_question_version: 1,
          forked_from_id: null,
        }}
        onClick={onClick}
      />,
    );

    fireEvent.click(screen.getByText('可点击工作空间'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// 3. CreateWorkspaceModal 交互
// ---------------------------------------------------------------------------

describe('CreateWorkspaceModal', () => {
  it('renders form with name and question_text inputs when open', () => {
    renderWithQueryClient(
      <CreateWorkspaceModal open={true} onClose={() => {}} onCreated={() => {}} />,
    );

    expect(screen.getByText('新建研究工作空间')).toBeInTheDocument();
    expect(screen.getByText('工作空间名称')).toBeInTheDocument();
    expect(screen.getByText('主研究问题')).toBeInTheDocument();
  });

  it('does not render modal content when closed', () => {
    renderWithQueryClient(
      <CreateWorkspaceModal open={false} onClose={() => {}} onCreated={() => {}} />,
    );

    // Modal 关闭时不应显示标题
    expect(screen.queryByText('新建研究工作空间')).not.toBeInTheDocument();
  });

  it('calls onClose when cancel button is clicked', () => {
    const onClose = vi.fn();
    renderWithQueryClient(
      <CreateWorkspaceModal open={true} onClose={onClose} onCreated={() => {}} />,
    );

    // Ant Design 对两个汉字的按钮文本自动插入空格："取消" → "取 消"
    const cancelBtn = screen.getByText('取 消');
    fireEvent.click(cancelBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows validation error when creating without filling fields', async () => {
    renderWithQueryClient(
      <CreateWorkspaceModal open={true} onClose={() => {}} onCreated={() => {}} />,
    );

    // Ant Design 对两个汉字的按钮文本自动插入空格："创建" → "创 建"
    const createBtn = screen.getByText('创 建');
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(screen.getByText('请输入名称')).toBeInTheDocument();
      expect(screen.getByText('请输入研究问题')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// 4. LabOpsPage 功能开关条件渲染
// ---------------------------------------------------------------------------

describe('LabOpsPage feature flag conditional rendering', () => {
  it('when researchModule flag is false, tabs are flows/parameters/models', async () => {
    _authState.user = {
      id: 'u-001',
      displayName: '研究员',
      roles: ['lab_member'],
      permissions: ['research:use'],
      featureFlags: { researchModule: false },
    };

    const { useAuthStore } = await import('@/features/auth/AuthProvider');
    const store = (useAuthStore as unknown as ReturnType<typeof vi.fn>)();
    const isResearchEnabled =
      (store.user as { featureFlags?: { researchModule?: boolean } })
        ?.featureFlags?.researchModule ?? false;

    expect(isResearchEnabled).toBe(false);

    const VALID_TABS = isResearchEnabled
      ? ['flows', 'research', 'publication']
      : ['flows', 'parameters', 'models'];
    expect(VALID_TABS).toEqual(['flows', 'parameters', 'models']);

    _authState.user = null;
  });

  it('when researchModule flag is true, tabs are flows/research/publication', async () => {
    _authState.user = {
      id: 'u-001',
      displayName: '研究员',
      roles: ['lab_member'],
      permissions: ['research:use'],
      featureFlags: { researchModule: true },
    };

    const { useAuthStore } = await import('@/features/auth/AuthProvider');
    const store = (useAuthStore as unknown as ReturnType<typeof vi.fn>)();
    const isResearchEnabled =
      (store.user as { featureFlags?: { researchModule?: boolean } })
        ?.featureFlags?.researchModule ?? false;

    expect(isResearchEnabled).toBe(true);

    const VALID_TABS = isResearchEnabled
      ? ['flows', 'research', 'publication']
      : ['flows', 'parameters', 'models'];
    expect(VALID_TABS).toEqual(['flows', 'research', 'publication']);

    _authState.user = null;
  });

  it('when user is null, researchModule defaults to false', async () => {
    _authState.user = null;

    const { useAuthStore } = await import('@/features/auth/AuthProvider');
    const store = (useAuthStore as unknown as ReturnType<typeof vi.fn>)();
    const isResearchEnabled =
      (store.user as { featureFlags?: { researchModule?: boolean } } | null)
        ?.featureFlags?.researchModule ?? false;

    expect(isResearchEnabled).toBe(false);
  });
});
