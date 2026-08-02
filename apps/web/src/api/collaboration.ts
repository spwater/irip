/**
 * Collaboration API — AI 助手协作功能 API 类型定义 + 请求函数。
 *
 * 端点（/api/v1/collaboration）：
 *   POST   /conversations/{id}/participants       — 邀请成员
 *   GET    /conversations/{id}/participants       — 列出参与者
 *   DELETE /conversations/{id}/participants/{uid}  — 移除成员
 *   POST   /conversations/{id}/leave              — 退出对话
 *   GET    /mentionable-users                     — 可 @ 用户列表
 */
import { http } from './client';

// ============================================================
// 类型定义
// ============================================================

/** 对话参与者 */
export type Participant = {
  user_id: string;
  display_name: string;
  avatar_url: string | null;
  role: 'owner' | 'member';
  joined_at: string;
};

/** 可 @ 的用户 */
export type MentionableUser = {
  id: string;
  display_name: string;
  avatar_url: string | null;
  roles: string[];
};

/** 对话筛选标签 */
export type ConversationTab = 'private' | 'collaborative';

// ============================================================
// API 响应类型
// ============================================================

type ParticipantApiResponse = {
  user_id: string;
  display_name: string;
  avatar_url: string | null;
  role: string;
  joined_at: string;
};

type ParticipantListApiResponse = { items: ParticipantApiResponse[] };

type MentionableUserApiResponse = {
  id: string;
  display_name: string;
  avatar_url: string | null;
  roles: string[];
};

type MentionableUserListApiResponse = { items: MentionableUserApiResponse[] };

// ============================================================
// API 函数
// ============================================================

/** 列出对话参与者 */
export async function apiListParticipants(convId: string): Promise<Participant[]> {
  const res = await http.get<ParticipantListApiResponse>(`/collaboration/conversations/${convId}/participants`);
  return res.data.items.map((p) => ({
    user_id: p.user_id,
    display_name: p.display_name,
    avatar_url: p.avatar_url,
    role: (p.role as 'owner' | 'member') ?? 'member',
    joined_at: p.joined_at,
  }));
}

/** 邀请成员加入对话 */
export async function apiInviteParticipant(convId: string, userId: string): Promise<Participant> {
  const res = await http.post<ParticipantApiResponse>(`/collaboration/conversations/${convId}/participants`, {
    user_id: userId,
  });
  return {
    user_id: res.data.user_id,
    display_name: res.data.display_name,
    avatar_url: res.data.avatar_url,
    role: (res.data.role as 'owner' | 'member') ?? 'member',
    joined_at: res.data.joined_at,
  };
}

/** 移除对话参与者 */
export async function apiRemoveParticipant(convId: string, userId: string): Promise<void> {
  await http.delete(`/collaboration/conversations/${convId}/participants/${userId}`);
}

/** 退出对话 */
export async function apiLeaveConversation(convId: string): Promise<void> {
  await http.post(`/collaboration/conversations/${convId}/leave`);
}

/** 列出可 @ 的用户（同 org active 用户） */
export async function apiListMentionableUsers(): Promise<MentionableUser[]> {
  const res = await http.get<MentionableUserListApiResponse>('/collaboration/mentionable-users');
  return res.data.items.map((u) => ({
    id: u.id,
    display_name: u.display_name,
    avatar_url: u.avatar_url,
    roles: u.roles ?? [],
  }));
}
