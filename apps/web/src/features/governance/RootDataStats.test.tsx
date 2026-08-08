import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import { apiGetRootDataStats } from '@/api/governance';
import { RootDataStats } from './RootDataStats';
import { useAuthStore } from '@/features/auth/AuthProvider';

vi.mock('@/api/governance', () => ({
  apiGetRootDataStats: vi.fn(),
}));

function renderStats(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <RootDataStats />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe('RootDataStats', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().reset();
  });

  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it('shows permission hint for non-admin users', () => {
    useAuthStore.setState({
      user: {
        id: 'u-researcher',
        displayName: '研究员',
        roles: ['researcher'],
        permissions: [],
      },
    });
    renderStats();
    expect(screen.getByText('Root 数据量统计')).toBeInTheDocument();
    expect(screen.getByText('仅平台管理员可查看此统计。')).toBeInTheDocument();
  });

  it('renders stats table for admin user', async () => {
    useAuthStore.setState({
      user: {
        id: 'u-admin',
        displayName: '管理员',
        roles: ['platform_administrator'],
        permissions: [],
      },
    });
    vi.mocked(apiGetRootDataStats).mockResolvedValueOnce({
      root_department_id: 'd-root',
      root_department_name: '公共数据',
      stats: [
        { table: 'fact', display_name: '事实记录', count: 1200 },
        { table: 'parameter', display_name: '参数', count: 350 },
      ],
    });
    renderStats();
    expect(await screen.findByText('事实记录')).toBeInTheDocument();
    expect(screen.getByText('参数')).toBeInTheDocument();
  });

  it('shows error alert when API fails', async () => {
    useAuthStore.setState({
      user: {
        id: 'u-admin',
        displayName: '管理员',
        roles: ['platform_administrator'],
        permissions: [],
      },
    });
    vi.mocked(apiGetRootDataStats).mockRejectedValueOnce(new Error('网络错误'));
    renderStats();
    expect(await screen.findByText('数据加载失败')).toBeInTheDocument();
  });

  it('shows total row with summed count', async () => {
    useAuthStore.setState({
      user: {
        id: 'u-admin',
        displayName: '管理员',
        roles: ['platform_administrator'],
        permissions: [],
      },
    });
    vi.mocked(apiGetRootDataStats).mockResolvedValueOnce({
      root_department_id: 'd-root',
      root_department_name: '公共数据',
      stats: [
        { table: 'fact', display_name: '事实记录', count: 100 },
        { table: 'parameter', display_name: '参数', count: 200 },
      ],
    });
    renderStats();
    expect(await screen.findByText('合计')).toBeInTheDocument();
  });
});
