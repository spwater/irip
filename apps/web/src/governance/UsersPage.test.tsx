import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock API client
vi.mock('@/api/client', () => ({
  apiListUsers: vi.fn(),
  apiListDepartments: vi.fn(),
  apiCreateUser: vi.fn(),
  apiDeleteUser: vi.fn(),
  apiRemoveRole: vi.fn(),
  apiUpdateUser: vi.fn(),
  apiUpdateUserStatus: vi.fn(),
  extractApiError: (err: unknown) => String(err),
}));
vi.mock('@/auth/AuthProvider', () => ({
  useAuthStore: () => ({
    user: { roles: ['platform_administrator'] },
  }),
}));

import { apiListUsers, apiListDepartments } from '@/api/client';
import { UsersPage } from '@/governance/UsersPage';
import type { UserListItem, UserListResponse, DepartmentListItem } from '@/api/client';

const mockUser: UserListItem = {
  id: 'u-001',
  email: 'admin@irip.local',
  display_name: '管理员',
  roles: ['platform_administrator'],
  status: 'active',
  department_id: 'dept-001',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const mockDept: DepartmentListItem = {
  id: 'dept-001',
  display_name: '研发部',
  code: 'rd',
  parent_id: null,
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
} as DepartmentListItem;

function renderWithProviders(ui: React.ReactElement): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('UsersPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListUsers).mockResolvedValue({
      items: [mockUser],
      next_cursor: null,
      has_more: false,
    } as UserListResponse);
    vi.mocked(apiListDepartments).mockResolvedValue({
      items: [mockDept],
      next_cursor: null,
      has_more: false,
    });
  });

  it('displays user display name, roles, department, and StatusMark', async () => {
    renderWithProviders(<UsersPage />);

    // Named region
    expect(await screen.findByRole('region', { name: '用户目录' })).toBeVisible();

    // Display name
    expect(screen.getByText('管理员')).toBeVisible();

    // Role label
    expect(screen.getByText('平台管理员')).toBeVisible();

    // Department
    expect(screen.getByText('研发部')).toBeVisible();

    // StatusMark text
    expect(screen.getByText('启用')).toBeVisible();
  });
});
