import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GovernanceConsole } from '@/governance/GovernanceConsole';

// Mock child tab pages to isolate shell structure assertions
vi.mock('@/governance/AIConfigPage', () => ({
  AIConfigPage: () => <div data-testid="ai-config">AIConfig</div>,
}));
vi.mock('@/governance/SystemHealthPage', () => ({
  SystemHealthPage: () => <div data-testid="system-health">SystemHealth</div>,
}));
vi.mock('@/governance/UsersPage', () => ({
  UsersPage: () => <div data-testid="users">Users</div>,
}));
vi.mock('@/governance/AuditPage', () => ({
  AuditPage: () => <div data-testid="audit">Audit</div>,
}));
vi.mock('@/jobs/JobsPage', () => ({
  JobsPage: () => <div data-testid="jobs">Jobs</div>,
}));

function renderWithProviders(ui: React.ReactElement): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('GovernanceConsole', () => {
  it('renders a level-1 heading', () => {
    renderWithProviders(<GovernanceConsole />);
    expect(screen.getByRole('heading', { level: 1, name: '平台治理' })).toBeVisible();
  });

  it('renders four tabs', () => {
    renderWithProviders(<GovernanceConsole />);
    expect(screen.getByText('系统配置')).toBeVisible();
    expect(screen.getByText('用户管理')).toBeVisible();
    expect(screen.getByText('审计事件')).toBeVisible();
    expect(screen.getByText('作业中心')).toBeVisible();
  });

  it('renders initial system-config tab content', () => {
    renderWithProviders(<GovernanceConsole />);
    expect(screen.getByTestId('ai-config')).toBeVisible();
    expect(screen.getByTestId('system-health')).toBeVisible();
  });
});
