import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PersonalSettings } from './PersonalSettings';
import type { Profile } from '@/api/account';

// Mock account API
vi.mock('@/api/account', async () => {
  const actual = await vi.importActual<typeof import('@/api/account')>('@/api/account');
  return {
    ...actual,
    apiGetProfile: vi.fn(),
    apiUpdateProfile: vi.fn(),
    apiChangePassword: vi.fn(),
    apiUploadAvatar: vi.fn(),
  };
});

// Mock useAuthStore
const mockLogout = vi.fn();
vi.mock('@/features/auth/AuthProvider', () => ({
  useAuthStore: vi.fn((selector) => selector({ logout: mockLogout })),
}));

// Mock extractApiError
vi.mock('@/api/types', () => ({
  extractApiError: (err: unknown): string => (err as Error)?.message ?? '操作失败',
}));

import {
  apiGetProfile,
  apiUpdateProfile,
  apiChangePassword,
  apiUploadAvatar,
} from '@/api/account';

const mockProfile: Profile = {
  id: 'u-001',
  email: 'test@irip.local',
  display_name: '研究员',
  avatar_url: 'http://example.com/a.png',
  roles: ['lab_member'],
  organization_id: 'org-001',
};

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe('PersonalSettings', () => {
  beforeEach(() => {
    vi.mocked(apiGetProfile).mockResolvedValue(mockProfile);
    vi.mocked(apiUpdateProfile).mockResolvedValue(mockProfile);
    vi.mocked(apiChangePassword).mockResolvedValue(undefined);
    vi.mocked(apiUploadAvatar).mockResolvedValue({ avatar_url: 'http://cdn/a.png' });
    mockLogout.mockResolvedValue(undefined);
  });

  it('renders three cards: 头像设置, 显示名设置, 修改密码', async () => {
    renderWithClient(<PersonalSettings />);
    await waitFor(() => {
      expect(screen.getByText('头像设置')).toBeInTheDocument();
      expect(screen.getByText('显示名设置')).toBeInTheDocument();
    });
    // 修改密码 appears as both Card title and Button text — use getAllByText
    expect(screen.getAllByText(/修\s*改\s*密\s*码/).length).toBeGreaterThanOrEqual(2);
  });

  it('loads and displays current display name in the input', async () => {
    renderWithClient(<PersonalSettings />);
    await waitFor(() => {
      expect(screen.getByDisplayValue('研究员')).toBeInTheDocument();
    });
  });

  it('shows 保存 button for display name', async () => {
    renderWithClient(<PersonalSettings />);
    // Ant Design 在中文字符间插入空格，用正则兼容
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /保\s*存/ })).toBeInTheDocument();
    });
  });

  it('calls apiUpdateProfile with trimmed display name when 保存 clicked', async () => {
    renderWithClient(<PersonalSettings />);
    const input = await screen.findByDisplayValue('研究员');
    await userEvent.clear(input);
    await userEvent.type(input, '新名字');
    const saveBtn = await screen.findByRole('button', { name: /保\s*存/ });
    await userEvent.click(saveBtn);
    await waitFor(() => {
      expect(apiUpdateProfile).toHaveBeenCalledWith({ display_name: '新名字' });
    });
  });

  it('renders password form fields: 旧密码, 新密码, 确认新密码', async () => {
    renderWithClient(<PersonalSettings />);
    await waitFor(() => {
      expect(screen.getByText('旧密码')).toBeInTheDocument();
      expect(screen.getByText('新密码')).toBeInTheDocument();
      expect(screen.getByText('确认新密码')).toBeInTheDocument();
    });
  });

  it('calls apiChangePassword and logout on success', async () => {
    renderWithClient(<PersonalSettings />);
    // Wait for profile to load
    await screen.findByDisplayValue('研究员');

    // Fill password form
    const passwordInputs = screen.getAllByPlaceholderText(/密码/);
    await userEvent.type(passwordInputs[0], 'Old-Pass-2026!');
    await userEvent.type(passwordInputs[1], 'New-Secret-2026!');
    await userEvent.type(passwordInputs[2], 'New-Secret-2026!');

    // Click 修改密码 button (use regex for Ant Design CJK spacing)
    const submitBtn = screen.getByRole('button', { name: /修\s*改\s*密\s*码/ });
    await userEvent.click(submitBtn);

    await waitFor(() => {
      expect(apiChangePassword).toHaveBeenCalledWith({
        old_password: 'Old-Pass-2026!',
        new_password: 'New-Secret-2026!',
      });
    });
    // logout should be called after success
    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalled();
    });
  });

  it('renders 上传头像 button', async () => {
    renderWithClient(<PersonalSettings />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /上\s*传\s*头\s*像/ })).toBeInTheDocument();
    });
  });

  it('renders static hint text for avatar format and re-login notice', async () => {
    renderWithClient(<PersonalSettings />);
    await waitFor(() => {
      // 头像格式提示
      expect(screen.getByText('支持 JPG/PNG/GIF，不超过 2MB')).toBeInTheDocument();
      // 改密码后重新登录提示
      expect(screen.getByText('修改成功后需重新登录')).toBeInTheDocument();
    });
  });
});
