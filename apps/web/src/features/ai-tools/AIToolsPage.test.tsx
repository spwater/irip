import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { apiListUnifiedTools, apiToggleAITool, type UnifiedToolDTO } from '@/api/models-ai';
import { AIToolsPage } from './AIToolsPage';

vi.mock('@/api/models-ai', () => ({
  apiListUnifiedTools: vi.fn(),
  apiToggleAITool: vi.fn(),
  apiCreateAITool: vi.fn(),
  apiUpdateAITool: vi.fn(),
}));

vi.mock('./ToolEditDrawer', () => ({
  ToolEditDrawer: ({ open, tool }: { open: boolean; tool: unknown }) =>
    open ? (
      <div data-testid="tool-edit-drawer">
        ToolEditDrawer {tool ? 'edit' : 'create'}
      </div>
    ) : null,
}));

vi.mock('./BuiltinToolEditDrawer', () => ({
  BuiltinToolEditDrawer: ({ open }: { open: boolean }) =>
    open ? <div data-testid="builtin-tool-edit-drawer">BuiltinToolEditDrawer</div> : null,
}));

const aiTool: UnifiedToolDTO = {
  name: 'search_standards',
  display_name: '搜索标准变量',
  description: '根据关键词搜索标准变量',
  enabled: true,
  status: 'active',
  lock_version: 1,
  updated_at: '2024-01-01T00:00:00Z',
  updated_by: 'admin',
  required_permission: 'standard:read',
  parameters_schema: { type: 'object' },
  category: 'ai_tool',
};

const builtinTool: UnifiedToolDTO = {
  name: 'xrd_parser',
  display_name: 'XRD 解析器',
  description: '解析 XRD 数据文件',
  enabled: true,
  status: 'active',
  lock_version: 1,
  updated_at: '2024-01-01T00:00:00Z',
  updated_by: 'admin',
  required_permission: 'ingestion:read',
  parameters_schema: { type: 'object' },
  category: 'ingestion',
};

const disabledAiTool: UnifiedToolDTO = {
  ...aiTool,
  name: 'query_facts',
  display_name: '查询实验数据',
  enabled: false,
};

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>{ui}</AntApp>
    </QueryClientProvider>,
  );
}

describe('AIToolsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListUnifiedTools).mockResolvedValue([aiTool, disabledAiTool, builtinTool]);
  });

  it('renders AItool and 内置工具 segmented tabs', () => {
    renderWithClient(<AIToolsPage />);
    expect(screen.getByText('AItool')).toBeInTheDocument();
    expect(screen.getByText('内置工具')).toBeInTheDocument();
  });

  it('shows AI tool rows in table after loading', async () => {
    renderWithClient(<AIToolsPage />);
    expect(await screen.findByText('搜索标准变量')).toBeInTheDocument();
    expect(screen.getByText('查询实验数据')).toBeInTheDocument();
  });

  it('renders 新建工具 button on AItool tab', () => {
    renderWithClient(<AIToolsPage />);
    expect(screen.getByRole('button', { name: /新建工具/ })).toBeInTheDocument();
  });

  it('opens ToolEditDrawer when 新建工具 clicked', async () => {
    renderWithClient(<AIToolsPage />);
    await userEvent.click(screen.getByRole('button', { name: /新建工具/ }));
    expect(await screen.findByTestId('tool-edit-drawer')).toBeInTheDocument();
  });

  it('filters AI tools by keyword', async () => {
    renderWithClient(<AIToolsPage />);
    await screen.findByText('搜索标准变量');
    const searchInput = screen.getByPlaceholderText('搜索工具名 / 显示名');
    await userEvent.type(searchInput, '标准');
    await waitFor(() => {
      expect(screen.getByText('搜索标准变量')).toBeInTheDocument();
      expect(screen.queryByText('查询实验数据')).not.toBeInTheDocument();
    });
  });

  it('switches to 内置工具 tab and shows builtin tools', async () => {
    renderWithClient(<AIToolsPage />);
    await screen.findByText('搜索标准变量');
    await userEvent.click(screen.getByText('内置工具'));
    expect(await screen.findByText('XRD 解析器')).toBeInTheDocument();
    expect(screen.queryByText('搜索标准变量')).not.toBeInTheDocument();
  });

  it('opens BuiltinToolEditDrawer when builtin 编辑 clicked', async () => {
    renderWithClient(<AIToolsPage />);
    await screen.findByText('搜索标准变量');
    await userEvent.click(screen.getByText('内置工具'));
    expect(await screen.findByText('XRD 解析器')).toBeInTheDocument();
    const editButtons = screen.getAllByRole('button', { name: /编\s*辑/ });
    await userEvent.click(editButtons[0]);
    expect(await screen.findByTestId('builtin-tool-edit-drawer')).toBeInTheDocument();
  });

  it('calls apiToggleAITool when switch toggled', async () => {
    vi.mocked(apiToggleAITool).mockResolvedValueOnce(aiTool);
    renderWithClient(<AIToolsPage />);
    await screen.findByText('搜索标准变量');
    // Find the switch by its role
    const switches = screen.getAllByRole('switch');
    await userEvent.click(switches[0]);
    // Modal.confirm appears; click 确定
    const okBtn = await screen.findByRole('button', { name: /确\s*定/ });
    await userEvent.click(okBtn);
    await waitFor(() => {
      expect(apiToggleAITool).toHaveBeenCalled();
    });
  });

  it('shows empty state when no AI tools returned', async () => {
    vi.mocked(apiListUnifiedTools).mockResolvedValueOnce([]);
    renderWithClient(<AIToolsPage />);
    await waitFor(() => {
      expect(screen.queryByText('搜索标准变量')).not.toBeInTheDocument();
    });
  });
});
