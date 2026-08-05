/**
 * useAssistantMutations — AssistantPage 所有 mutation / action 逻辑。
 *
 * 从 AssistantPage.tsx 提取。统一管理对话操作（置顶、归档、删除、新建）、
 * 协作成员管理（邀请、移除）、实验数据插入等 action。
 */

import { useCallback } from 'react';
import { message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import {
  apiCreateConversation,
  apiDeleteConversation,
  apiToggleArchive,
  apiTogglePin,
} from '@/api/models-ai';
import { apiInviteParticipant, apiRemoveParticipant } from '@/api/collaboration';
import { apiGetFactData } from '@/api/facts-provenance';
import { extractApiError, type FactSummary } from '@/api/types';
import { compactJson } from '@/shared/json-utils';

export interface UseAssistantMutationsParams {
  selectedConvId: string | null;
  setSelectedConvId: (id: string | null) => void;
  setSelectedFactIds: React.Dispatch<React.SetStateAction<string[]>>;
  selectedFactIds: string[];
  setFactModalOpen: (v: boolean) => void;
  setInsertingFact: (v: boolean) => void;
  setFactContext: (v: string | null) => void;
  setFactContextLabel: (v: string | null) => void;
  setLocalMessages: React.Dispatch<React.SetStateAction<import('@/api/models-ai').AssistantMessage[]>>;
  setStreamingAnswer: React.Dispatch<React.SetStateAction<string | null>>;
  factsData: { items: FactSummary[] } | undefined;
}

export function useAssistantMutations(params: UseAssistantMutationsParams) {
  const {
    selectedConvId,
    setSelectedConvId,
    setSelectedFactIds,
    selectedFactIds,
    setFactModalOpen,
    setInsertingFact,
    setFactContext,
    setFactContextLabel,
    setLocalMessages,
    setStreamingAnswer,
    factsData,
  } = params;

  const queryClient = useQueryClient();

  // ---- 新建对话 ----
  const handleNewConversation = useCallback(async (): Promise<void> => {
    try {
      const conv = await apiCreateConversation({
        title: '',
        provider_mode: 'openai_compatible',
      });
      setSelectedConvId(conv.id);
      setLocalMessages([]);
      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
    } catch (err) {
      message.error(extractApiError(err));
    }
  }, [setSelectedConvId, setLocalMessages, queryClient]);

  // ---- 置顶 / 取消置顶 ----
  const handleTogglePin = useCallback((convId: string): void => {
    apiTogglePin(convId).then(() => {
      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
    }).catch((err) => message.error(extractApiError(err)));
  }, [queryClient]);

  // ---- 归档 / 取消归档 ----
  const handleToggleArchive = useCallback((convId: string): void => {
    apiToggleArchive(convId).then(() => {
      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
      if (selectedConvId === convId) {
        setSelectedConvId(null);
      }
    }).catch((err) => message.error(extractApiError(err)));
  }, [queryClient, selectedConvId, setSelectedConvId]);

  // ---- 永久删除对话 ----
  const handleDeleteConversation = useCallback((convId: string): void => {
    apiDeleteConversation(convId).then(() => {
      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
      if (selectedConvId === convId) {
        setSelectedConvId(null);
        setLocalMessages([]);
        setStreamingAnswer(null);
        void queryClient.removeQueries({ queryKey: ['assistant-messages', convId] });
        void queryClient.removeQueries({ queryKey: ['participants', convId] });
        void queryClient.removeQueries({ queryKey: ['showcase-items', convId] });
      }
      message.success('对话已删除');
    }).catch((err) => message.error(extractApiError(err)));
  }, [queryClient, selectedConvId, setSelectedConvId, setLocalMessages, setStreamingAnswer]);

  // ---- 邀请 / 移除成员（InviteModal onOk） ----
  const handleInviteModalOk = useCallback(async (
    inviteUserIds: string[],
    participantsData: { user_id: string; role: string }[] | undefined,
  ): Promise<void> => {
    if (!selectedConvId) return;
    const currentParticipantIds = new Set((participantsData ?? [])
      .filter((p) => p.role !== 'owner')
      .map((p) => p.user_id));
    const selectedIds = new Set(inviteUserIds);
    // 需要邀请的：选中的但不在当前参与者里
    const toInvite = inviteUserIds.filter((id) => !currentParticipantIds.has(id));
    // 需要移除的：在当前参与者里但没选中的
    const toRemove = [...currentParticipantIds].filter((id) => !selectedIds.has(id));

    let okCount = 0;
    let failCount = 0;
    for (const uid of toInvite) {
      try { await apiInviteParticipant(selectedConvId, uid); okCount++; } catch { failCount++; }
    }
    for (const uid of toRemove) {
      try { await apiRemoveParticipant(selectedConvId, uid); okCount++; } catch { failCount++; }
    }

    void queryClient.invalidateQueries({ queryKey: ['participants', selectedConvId] });
    void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
    if (okCount > 0 && failCount === 0) message.success(`操作成功（${okCount} 项变更）`);
    else if (failCount > 0) message.warning(`${okCount} 项成功，${failCount} 项失败`);
    else message.info('无变更');
  }, [selectedConvId, queryClient]);

  // ---- 移除参与者（ParticipantDrawer） ----
  const handleRemoveParticipant = useCallback(async (convId: string, userId: string): Promise<void> => {
    try {
      await apiRemoveParticipant(convId, userId);
      void queryClient.invalidateQueries({ queryKey: ['participants', convId] });
      message.success('成员已移除');
    } catch (err) {
      message.error(extractApiError(err));
    }
  }, [queryClient]);

  // ---- 插入实验数据 ----
  const handleInsertFact = useCallback(async (): Promise<void> => {
    if (selectedFactIds.length === 0) {
      message.warning('请至少选择一个样品');
      return;
    }
    setInsertingFact(true);
    try {
      const allData: string[] = [];
      const labels: string[] = [];
      for (const factId of selectedFactIds) {
        const data = await apiGetFactData(factId);
        const fact = (factsData?.items ?? []).find((f: FactSummary) => f.fact_id === factId);
        const label = fact?.subject_id ?? factId;
        labels.push(label);
        // 传完整的 metadata + points + series（紧凑序列化，去掉多余空格减少 token 消耗）
        const compact = { metadata: data.metadata, points: data.points, series: data.series };
        allData.push(`### 样品: ${label}\n\`\`\`json\n${compactJson(compact)}\n\`\`\``);
      }
      const context = `以下是实验数据，请基于此数据回答用户的问题：\n\n${allData.join('\n\n')}`;
      setFactContext(context);
      setFactContextLabel(labels.join(', '));
      setFactModalOpen(false);
      message.success(`已加载 ${labels.length} 个样品的实验数据`);
    } catch (err) {
      message.error(`获取数据失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setInsertingFact(false);
    }
  }, [selectedFactIds, setInsertingFact, factsData, setFactContext, setFactContextLabel, setFactModalOpen]);

  // ---- 清除实验数据上下文 ----
  const handleClearFactContext = useCallback((): void => {
    setFactContext(null);
    setFactContextLabel(null);
    setSelectedFactIds([]);
  }, [setFactContext, setFactContextLabel, setSelectedFactIds]);

  // ---- 全选 / 取消全选 ----
  const handleSelectAll = useCallback((allFilteredFactIds: string[]): void => {
    if (allFilteredFactIds.length > 0 && allFilteredFactIds.every((id) => selectedFactIds.includes(id))) {
      setSelectedFactIds((prev) => prev.filter((id) => !allFilteredFactIds.includes(id)));
    } else {
      setSelectedFactIds((prev) => Array.from(new Set([...prev, ...allFilteredFactIds])));
    }
  }, [selectedFactIds, setSelectedFactIds]);

  // ---- 切换单个事实 ----
  const handleToggleFact = useCallback((factId: string): void => {
    setSelectedFactIds((prev) =>
      prev.includes(factId) ? prev.filter((id) => id !== factId) : [...prev, factId],
    );
  }, [setSelectedFactIds]);

  // ---- 切换分组全选 ----
  const handleToggleGroup = useCallback((groupFactIds: string[]): void => {
    const allInGroup = groupFactIds.every((id) => selectedFactIds.includes(id));
    if (allInGroup) {
      setSelectedFactIds((prev) => prev.filter((id) => !groupFactIds.includes(id)));
    } else {
      setSelectedFactIds((prev) => Array.from(new Set([...prev, ...groupFactIds])));
    }
  }, [selectedFactIds, setSelectedFactIds]);

  // ---- 定位原文（从橱窗卡片跳转到消息区对应块） ----
  const handleLocateMessage = useCallback((messageId: string, blockIndex: number): void => {
    const msgEl = document.getElementById(`msg-${messageId}`);
    if (!msgEl) {
      message.warning('原消息已不存在');
      return;
    }
    // 滚动到消息位置
    msgEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    // 查找目标块并高亮
    setTimeout(() => {
      const blockEl = msgEl.querySelector(`[data-block-id="${messageId}-${blockIndex}"]`);
      if (blockEl) {
        blockEl.classList.add('highlight');
        setTimeout(() => {
          blockEl.classList.remove('highlight');
        }, 2500);
      }
    }, 300);
  }, []);

  return {
    handleNewConversation,
    handleTogglePin,
    handleToggleArchive,
    handleDeleteConversation,
    handleInviteModalOk,
    handleRemoveParticipant,
    handleInsertFact,
    handleClearFactContext,
    handleSelectAll,
    handleToggleFact,
    handleToggleGroup,
    handleLocateMessage,
  };
}
