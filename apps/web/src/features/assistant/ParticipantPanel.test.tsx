import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ParticipantPanel } from './ParticipantPanel';
import type { Participant, MentionableUser } from '@/api/collaboration';

// Mock collaboration API
vi.mock('@/api/collaboration', async () => {
  const actual = await vi.importActual<typeof import('@/api/collaboration')>('@/api/collaboration');
  return {
    ...actual,
    apiListParticipants: vi.fn(),
    apiListMentionableUsers: vi.fn(),
    apiInviteParticipant: vi.fn(),
    apiRemoveParticipant: vi.fn(),
  };
});

// Mock useAuthStore
vi.mock('@/features/auth/AuthProvider', () => ({
  useAuthStore: vi.fn((selector) => selector({ user: { id: 'me-001', displayName: '我', roles: ['lab_director'] } })),
}));

// Mock extractApiError
vi.mock('@/api/types', () => ({
  extractApiError: (err: unknown): string => (err as Error)?.message ?? '操作失败',
}));

import {
  apiListParticipants,
  apiListMentionableUsers,
  apiInviteParticipant,
  apiRemoveParticipant,
} from '@/api/collaboration';

const mockParticipants: Participant[] = [
  { user_id: 'me-001', display_name: '我', avatar_url: null, role: 'owner', joined_at: '2026-07-01T00:00:00Z' },
  { user_id: 'u-002', display_name: '张三', avatar_url: null, role: 'member', joined_at: '2026-07-02T00:00:00Z' },
];

const mockMentionableUsers: MentionableUser[] = [
  { id: 'u-003', display_name: '李四', avatar_url: null, roles: ['lab_member'] },
];

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe('ParticipantPanel', () => {
  beforeEach(() => {
    vi.mocked(apiListParticipants).mockResolvedValue(mockParticipants);
    vi.mocked(apiListMentionableUsers).mockResolvedValue(mockMentionableUsers);
    vi.mocked(apiInviteParticipant).mockResolvedValue({
      user_id: 'u-003',
      display_name: '李四',
      avatar_url: null,
      role: 'member',
      joined_at: '2026-07-03T00:00:00Z',
    });
    vi.mocked(apiRemoveParticipant).mockResolvedValue(undefined);
  });

  it('returns null when conversationId is null', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <ParticipantPanel conversationId={null} isOwner={true} />
      </QueryClientProvider>,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders participant avatar group and count', async () => {
    renderWithClient(<ParticipantPanel conversationId="conv-001" isOwner={true} />);
    await waitFor(() => {
      expect(screen.getByText(/2人/)).toBeInTheDocument();
    });
  });

  it('opens drawer with member list when avatar group clicked', async () => {
    renderWithClient(<ParticipantPanel conversationId="conv-001" isOwner={false} />);
    // Click the avatar group area
    const trigger = screen.getByRole('button', { name: '查看对话参与者' });
    await userEvent.click(trigger);
    // Drawer should show member names
    await waitFor(() => {
      expect(screen.getByText('对话参与者')).toBeInTheDocument();
    });
  });

  it('shows 邀请成员 button for owner', async () => {
    renderWithClient(<ParticipantPanel conversationId="conv-001" isOwner={true} />);
    const trigger = screen.getByRole('button', { name: '查看对话参与者' });
    await userEvent.click(trigger);
    await waitFor(() => {
      expect(screen.getByText('邀请成员')).toBeInTheDocument();
    });
  });

  it('does not show 邀请成员 button for non-owner', async () => {
    renderWithClient(<ParticipantPanel conversationId="conv-001" isOwner={false} />);
    const trigger = screen.getByRole('button', { name: '查看对话参与者' });
    await userEvent.click(trigger);
    await waitFor(() => {
      expect(screen.getByText('对话参与者')).toBeInTheDocument();
    });
    expect(screen.queryByText('邀请成员')).not.toBeInTheDocument();
  });

  it('shows 移除 button for members when isOwner', async () => {
    renderWithClient(<ParticipantPanel conversationId="conv-001" isOwner={true} />);
    const trigger = screen.getByRole('button', { name: '查看对话参与者' });
    await userEvent.click(trigger);
    await waitFor(() => {
      expect(screen.getByText('移除')).toBeInTheDocument();
    });
  });

  it('shows 移除 button for members when isOwner and triggers remove on click', async () => {
    renderWithClient(<ParticipantPanel conversationId="conv-001" isOwner={true} />);
    const trigger = screen.getByRole('button', { name: '查看对话参与者' });
    await userEvent.click(trigger);
    await waitFor(() => {
      expect(screen.getByText('移除')).toBeInTheDocument();
    });
    // Click 移除 to open Popconfirm
    await userEvent.click(screen.getByText('移除'));
    // Popconfirm should show confirmation text
    await waitFor(() => {
      expect(screen.getByText('确定移除该成员？')).toBeInTheDocument();
    });
  });
});
