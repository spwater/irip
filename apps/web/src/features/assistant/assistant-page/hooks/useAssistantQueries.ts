/**
 * useAssistantQueries — AssistantPage 所有 useQuery 声明 + 派生数据。
 *
 * 从 AssistantPage.tsx 提取。统一管理所有数据查询，
 * 供主组件和子组件使用。
 */

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  apiGetProviderStatus,
  apiListConversations,
  apiListMessages,
  type ConversationSummary,
} from '@/api/models-ai';
import { apiListParticipants, apiListMentionableUsers } from '@/api/collaboration';
import { apiListAllFacts } from '@/api/facts-provenance';
import { useAuthStore } from '@/features/auth/AuthProvider';
import type { UseAssistantQueriesParams, UseAssistantQueriesResult } from '../types';

export function useAssistantQueries(params: UseAssistantQueriesParams): UseAssistantQueriesResult {
  const { selectedConvId, isSending, factModalOpen, inviteModalOpen, showArchived, searchKeyword, activeTab } = params;
  const currentUser = useAuthStore((s) => s.user);

  // ---- 对话列表查询（支持关键词搜索 + 三栏筛选） ----
  const { data: conversations } = useQuery({
    queryKey: ['assistant-conversations', showArchived, searchKeyword || undefined, activeTab],
    queryFn: () => apiListConversations({
      archivedOnly: showArchived,
      keyword: searchKeyword || undefined,
      tab: activeTab,
    }),
    retry: false,
    refetchInterval: 30_000, // irip-ai-collab: 30 秒轮询刷新
  });

  const conversationList: ConversationSummary[] = conversations ?? [];

  // irip-ai-collab: 查询参与者（判断当前用户是否为 owner）
  const { data: participantsData } = useQuery({
    queryKey: ['participants', selectedConvId],
    queryFn: () => apiListParticipants(selectedConvId!),
    enabled: !!selectedConvId,
    retry: false,
  });

  // ---- 消息列表查询（依赖选中对话） ----
  // P2-C15: 降低轮询频率 3s→10s，仅协作对话轮询，减少不必要请求
  // 后端已有 /api/v1/assistant/stream SSE 端点（用于 AI 流式回答），
  // 但消息列表更新仍需轮询（SSE 仅覆盖 AI 回答，不覆盖其他参与者发消息）
  const isCollaborativeConv = useMemo(() => {
    if (!selectedConvId) return false;
    const participants = participantsData ?? [];
    return participants.length > 1;
  }, [selectedConvId, participantsData]);

  const { data: messagesData } = useQuery({
    queryKey: ['assistant-messages', selectedConvId],
    queryFn: () => apiListMessages(selectedConvId!),
    enabled: !!selectedConvId,
    retry: false,
    staleTime: 5_000, // 5 秒内不重复请求
    refetchInterval: selectedConvId && !isSending && isCollaborativeConv ? 10_000 : false,
    refetchOnWindowFocus: true, // 切回窗口时刷新（替代持续轮询）
  });

  // irip-ai-collab: 可邀请用户（同 org active 用户）
  const { data: mentionableUsersData } = useQuery({
    queryKey: ['mentionable-users'],
    queryFn: apiListMentionableUsers,
    enabled: inviteModalOpen,
    staleTime: 60_000,
  });

  // AI Provider 状态（在线/离线）
  const { data: providerStatusData } = useQuery({
    queryKey: ['assistant-provider-status'],
    queryFn: () => apiGetProviderStatus(),
    retry: false,
    staleTime: 30_000,
  });
  const aiOnline = (providerStatusData?.provider_mode ?? 'offline') !== 'offline';

  // 查询事实列表（用于插入实验数据）
  const { data: factsData } = useQuery({
    queryKey: ['facts-for-insert'],
    queryFn: () => apiListAllFacts({ page_size: 100 }),
    enabled: factModalOpen,
  });

  // 判断当前用户是否为选中对话的 owner
  // irip-ai-collab: 优先从 participant 记录判断，兼容旧对话（无 participant 记录时按创建者判断）
  const isOwner = useMemo(() => {
    if (!selectedConvId || !currentUser) return false;
    const participants = participantsData ?? [];
    // 优先从 participant 记录判断
    if (participants.length > 0) {
      return participants.some((p) => p.user_id === currentUser.id && p.role === 'owner');
    }
    // 兼容旧对话：无 participant 记录时，创建者即 owner
    const conv = conversationList.find((c) => c.id === selectedConvId);
    return conv?.user_id === currentUser.id;
  }, [selectedConvId, currentUser, participantsData, conversationList]);

  // irip-ai-collab: 判断当前对话是否为协作对话（参与者 > 1）
  const isCollaborative = useMemo(() => {
    const participants = participantsData ?? [];
    return participants.length > 1;
  }, [participantsData]);

  return {
    conversationList,
    messagesData,
    participantsData,
    mentionableUsersData,
    factsData,
    aiOnline,
    isOwner,
    isCollaborative,
  };
}
