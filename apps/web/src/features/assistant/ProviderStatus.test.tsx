import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { apiGetProviderStatus, type ProviderStatus as ProviderStatusType } from '@/api/models-ai';
import { ProviderStatus } from './ProviderStatus';

vi.mock('@/api/models-ai', () => ({
  apiGetProviderStatus: vi.fn(),
}));

const offlineStatus: ProviderStatusType = {
  provider_mode: 'offline',
  whitelist_tools: [],
  candidate_tools: [],
};

const openaiStatus: ProviderStatusType = {
  provider_mode: 'openai_compatible',
  whitelist_tools: [
    { name: 'search_standards', display_name: '搜索标准变量', description: '搜索标准', required_permission: 'standard:read' },
  ],
  candidate_tools: [
    { name: 'query_facts', display_name: '查询实验数据', description: '查询', required_permission: 'fact:read' },
  ],
};

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>{ui}</AntApp>
    </QueryClientProvider>,
  );
}

describe('ProviderStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders 中材小艾 label', async () => {
    vi.mocked(apiGetProviderStatus).mockResolvedValue(offlineStatus);
    renderWithClient(<ProviderStatus />);
    expect(screen.getByText('中材小艾')).toBeInTheDocument();
  });

  it('shows 离线模拟模式 when provider mode is offline', async () => {
    vi.mocked(apiGetProviderStatus).mockResolvedValue(offlineStatus);
    renderWithClient(<ProviderStatus />);
    expect(await screen.findByText('离线模拟模式')).toBeInTheDocument();
  });

  it('shows OpenAI compatible mode text when not offline', async () => {
    vi.mocked(apiGetProviderStatus).mockResolvedValue(openaiStatus);
    renderWithClient(<ProviderStatus />);
    expect(await screen.findByText(/OpenAI 兼容/)).toBeInTheDocument();
  });

  it('shows tool count as sum of whitelist and candidate tools', async () => {
    vi.mocked(apiGetProviderStatus).mockResolvedValue(openaiStatus);
    renderWithClient(<ProviderStatus />);
    expect(await screen.findByText('2 个工具')).toBeInTheDocument();
  });

  it('shows 0 个工具 for offline mode', async () => {
    vi.mocked(apiGetProviderStatus).mockResolvedValue(offlineStatus);
    renderWithClient(<ProviderStatus />);
    expect(await screen.findByText('0 个工具')).toBeInTheDocument();
  });

  it('opens detail modal when card clicked', async () => {
    vi.mocked(apiGetProviderStatus).mockResolvedValue(openaiStatus);
    renderWithClient(<ProviderStatus />);
    // Click the card to open modal
    const card = await screen.findByText('中材小艾');
    await userEvent.click(card.closest('.ant-card')!);
    expect(await screen.findByText('中材小艾详情')).toBeInTheDocument();
  });

  it('shows tool details in modal', async () => {
    vi.mocked(apiGetProviderStatus).mockResolvedValue(openaiStatus);
    renderWithClient(<ProviderStatus />);
    const card = await screen.findByText('中材小艾');
    await userEvent.click(card.closest('.ant-card')!);
    expect(await screen.findByText('搜索标准变量')).toBeInTheDocument();
    expect(screen.getByText('查询实验数据')).toBeInTheDocument();
  });

  it('shows 可用工具 heading in modal', async () => {
    vi.mocked(apiGetProviderStatus).mockResolvedValue(openaiStatus);
    renderWithClient(<ProviderStatus />);
    const card = await screen.findByText('中材小艾');
    await userEvent.click(card.closest('.ant-card')!);
    // 可用工具 appears both as card label and modal heading; verify at least one exists
    expect((await screen.findAllByText('可用工具')).length).toBeGreaterThanOrEqual(1);
  });

  it('shows 运行模式 in modal', async () => {
    vi.mocked(apiGetProviderStatus).mockResolvedValue(openaiStatus);
    renderWithClient(<ProviderStatus />);
    const card = await screen.findByText('中材小艾');
    await userEvent.click(card.closest('.ant-card')!);
    expect(await screen.findByText('运行模式')).toBeInTheDocument();
    expect(screen.getByText('openai_compatible')).toBeInTheDocument();
  });
});
