import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock API client
vi.mock('@/api/client', () => ({
  apiListAuditEvents: vi.fn(),
  apiCreateAuditExport: vi.fn(),
  extractApiError: (err: unknown) => String(err),
}));
vi.mock('@/auth/AuthProvider', () => ({
  useAuthStore: () => ({
    user: { permissions: ['audit:read'] },
  }),
}));

import { apiListAuditEvents } from '@/api/client';
import { AuditPage } from '@/governance/AuditPage';
import type { AuditEventItem, AuditEventListResponse } from '@/api/client';

const mockEvent: AuditEventItem = {
  id: 'evt-001',
  occurred_at: '2026-07-28T10:30:00Z',
  actor_user_id: 'u-admin-001',
  organization_id: 'org-001',
  action: 'governance.user.assign_roles',
  resource_type: 'app_user',
  resource_id: 'u-002',
  payload: { result: 'success' },
  ip: '192.168.1.1',
  user_agent: 'Mozilla/5.0',
};

function renderWithProviders(ui: React.ReactElement): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('AuditPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListAuditEvents).mockResolvedValue({
      items: [mockEvent],
      next_cursor: null,
      has_more: false,
    } as AuditEventListResponse);
  });

  it('displays audit event time, actor, action, resource, and named region', async () => {
    renderWithProviders(<AuditPage />);

    // Named region
    expect(await screen.findByRole('region', { name: '审计事件目录' })).toBeVisible();

    // Action
    expect(screen.getByText('governance.user.assign_roles')).toBeVisible();

    // Resource type
    expect(screen.getByText('app_user')).toBeVisible();

    // Explicit result status
    expect(screen.getByText('成功')).toBeVisible();
  });
});
