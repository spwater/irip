import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock API client
vi.mock('@/api/client', () => ({
  apiGetSystemHealth: vi.fn(),
}));
vi.mock('@/auth/AuthProvider', () => ({
  useAuthStore: () => ({
    user: { permissions: ['system:health'] },
  }),
}));

import { apiGetSystemHealth } from '@/api/client';
import { SystemHealthPage } from '@/governance/SystemHealthPage';
import type { SystemHealth } from '@/api/client';

const mockHealth: SystemHealth = {
  status: 'degraded',
  checks: [
    { name: 'database', status: 'ok', latency_ms: 5, message: 'version: 42' },
    { name: 'redis', status: 'error', latency_ms: null, message: 'Connection refused' },
  ],
  migration_version: '0042',
  worker_heartbeat: '2026-07-28T10:00:00Z',
  outbox_backlog: 250,
};

function renderWithProviders(ui: React.ReactElement): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('SystemHealthPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('displays health status, version/error details, and backlog', async () => {
    vi.mocked(apiGetSystemHealth).mockResolvedValue(mockHealth);
    renderWithProviders(<SystemHealthPage />);

    // Wait for query to resolve
    expect(await screen.findByText(/系统状态/)).toBeVisible();

    // Named region
    expect(screen.getByRole('region', { name: '系统健康状态' })).toBeVisible();

    // Version detail
    expect(screen.getByText(/version: 42/)).toBeVisible();

    // Error detail
    expect(screen.getByText(/Connection refused/)).toBeVisible();

    // Backlog value
    expect(screen.getByText('250')).toBeVisible();
  });

  it('shows retry action on API failure without empty-data message', async () => {
    vi.mocked(apiGetSystemHealth).mockRejectedValue(new Error('Network error'));
    renderWithProviders(<SystemHealthPage />);

    expect(await screen.findByText('系统健康状态获取失败')).toBeVisible();
    expect(screen.getByText('重试')).toBeVisible();
  });
});
