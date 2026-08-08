import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { apiCreateAITool, apiUpdateAITool } from '@/api/models-ai';
import { ToolEditDrawer } from './ToolEditDrawer';
import type { AIToolDTO } from './types';

vi.mock('@/api/models-ai', () => ({
  apiCreateAITool: vi.fn(),
  apiUpdateAITool: vi.fn(),
}));

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>{ui}</AntApp>
    </QueryClientProvider>,
  );
}

const existingTool: AIToolDTO = {
  name: 'search_standards',
  display_name: '搜索标准变量',
  description: '根据关键词搜索标准变量',
  required_permission: 'standard:read',
  parameters_schema: { type: 'object', properties: { keyword: { type: 'string' } } },
  enabled: true,
  lock_version: 2,
  updated_at: '2024-01-01T00:00:00Z',
  updated_by: 'admin',
};

describe('ToolEditDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows 新建工具 title when tool is null', () => {
    renderWithClient(<ToolEditDrawer open={true} tool={null} onClose={vi.fn()} />);
    expect(screen.getByText('新建工具')).toBeInTheDocument();
  });

  it('shows 编辑工具 title when tool provided', () => {
    renderWithClient(<ToolEditDrawer open={true} tool={existingTool} onClose={vi.fn()} />);
    expect(screen.getByText('编辑工具')).toBeInTheDocument();
  });

  it('shows 仅创建声明层 alert in create mode', () => {
    renderWithClient(<ToolEditDrawer open={true} tool={null} onClose={vi.fn()} />);
    expect(screen.getByText('仅创建声明层')).toBeInTheDocument();
  });

  it('renders all form labels', () => {
    renderWithClient(<ToolEditDrawer open={true} tool={null} onClose={vi.fn()} />);
    expect(screen.getByText('工具名')).toBeInTheDocument();
    expect(screen.getByText('显示名')).toBeInTheDocument();
    expect(screen.getByText('描述')).toBeInTheDocument();
    expect(screen.getByText('所需权限')).toBeInTheDocument();
    expect(screen.getByText('参数 Schema (JSON)')).toBeInTheDocument();
  });

  it('disables name input in edit mode', () => {
    renderWithClient(<ToolEditDrawer open={true} tool={existingTool} onClose={vi.fn()} />);
    const nameInput = screen.getByPlaceholderText('如 search_standards');
    expect(nameInput).toBeDisabled();
  });

  it('prefills form fields in edit mode', () => {
    renderWithClient(<ToolEditDrawer open={true} tool={existingTool} onClose={vi.fn()} />);
    expect(screen.getByDisplayValue('搜索标准变量')).toBeInTheDocument();
    expect(screen.getByDisplayValue('standard:read')).toBeInTheDocument();
  });

  it('shows 合法 JSON help text for valid schema', () => {
    renderWithClient(<ToolEditDrawer open={true} tool={null} onClose={vi.fn()} />);
    expect(screen.getByText('合法 JSON')).toBeInTheDocument();
  });

  it('shows schema error for invalid JSON', async () => {
    renderWithClient(<ToolEditDrawer open={true} tool={null} onClose={vi.fn()} />);
    const schemaTextarea = screen.getByPlaceholderText('{"type": "object", "properties": {...}}');
    await userEvent.type(schemaTextarea, 'invalid');
    // The "合法 JSON" success text should be replaced by error state
    await waitFor(() => {
      expect(screen.queryByText('合法 JSON')).not.toBeInTheDocument();
    });
  });

  it('disables save button when schema has error', async () => {
    renderWithClient(<ToolEditDrawer open={true} tool={null} onClose={vi.fn()} />);
    const schemaTextarea = screen.getByPlaceholderText('{"type": "object", "properties": {...}}');
    await userEvent.type(schemaTextarea, 'invalid');
    const saveBtn = screen.getByRole('button', { name: /保\s*存/ });
    expect(saveBtn).toBeDisabled();
  });

  it('shows validation error when saving without required fields', async () => {
    renderWithClient(<ToolEditDrawer open={true} tool={null} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: /保\s*存/ }));
    expect(await screen.findByText('请输入工具名')).toBeInTheDocument();
  });

  it('calls apiCreateAITool on valid create submit', async () => {
    vi.mocked(apiCreateAITool).mockResolvedValueOnce(existingTool);
    renderWithClient(<ToolEditDrawer open={true} tool={null} onClose={vi.fn()} />);
    await userEvent.type(screen.getByPlaceholderText('如 search_standards'), 'new_tool');
    await userEvent.type(screen.getByPlaceholderText('如 搜索标准变量'), '新工具');
    await userEvent.type(screen.getByPlaceholderText('工具描述，供 AI 理解工具用途'), '描述内容');
    await userEvent.type(screen.getByPlaceholderText('如 standard:read'), 'tool:read');
    await userEvent.click(screen.getByRole('button', { name: /保\s*存/ }));
    await waitFor(() => {
      expect(apiCreateAITool).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'new_tool', display_name: '新工具' }),
      );
    });
  });

  it('calls apiUpdateAITool on valid edit submit', async () => {
    vi.mocked(apiUpdateAITool).mockResolvedValueOnce(existingTool);
    renderWithClient(<ToolEditDrawer open={true} tool={existingTool} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: /保\s*存/ }));
    await waitFor(() => {
      expect(apiUpdateAITool).toHaveBeenCalledWith(
        'search_standards',
        expect.objectContaining({ lock_version: 2 }),
      );
    });
  });

  it('shows lock version and enabled status in edit mode', () => {
    renderWithClient(<ToolEditDrawer open={true} tool={existingTool} onClose={vi.fn()} />);
    expect(screen.getByText(/乐观锁版本: 2/)).toBeInTheDocument();
    expect(screen.getByText(/启用状态: 已启用/)).toBeInTheDocument();
  });
});
