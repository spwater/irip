/**
 * AI 助手主页面 — 编排层。
 *
 * 从 1179 行单函数组件精简为编排层：
 * 调用 hooks 获取数据 + 组合子组件，目标 < 300 行。
 * 所有业务逻辑已提取到 assistant-page/hooks/ 和 assistant-page/components/ 子目录。
 *
 * 改进：
 * 1. 进入页面直接可对话，首次发言自动创建对话，标题根据首条消息自动生成
 * 2. 对话消息持久化，刷新页面后历史消息保留
 * 3. 用户消息立即显示，AI 回答逐字流式输出
 */

import { useEffect, useMemo, useState } from 'react';
import ShowcasePanel from '@/features/assistant/ShowcasePanel';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { useAssistantQueries } from '@/features/assistant/assistant-page/hooks/useAssistantQueries';
import { useStreamingAnswer } from '@/features/assistant/assistant-page/hooks/useStreamingAnswer';
import { useAssistantMutations } from '@/features/assistant/assistant-page/hooks/useAssistantMutations';
import { useContainerHeight } from '@/features/assistant/assistant-page/utils/useContainerHeight';
import { buildFactGroups, flattenFactIds } from '@/features/assistant/assistant-page/utils/factGroups';
import { mergeDisplayMessages } from '@/features/assistant/assistant-page/utils/displayMessages';
import { ConversationSidebar } from '@/features/assistant/assistant-page/components/ConversationSidebar';
import { MessageList } from '@/features/assistant/assistant-page/components/MessageList';
import { MessageInputArea } from '@/features/assistant/assistant-page/components/MessageInputArea';
import { InviteModal } from '@/features/assistant/assistant-page/components/InviteModal';
import { ParticipantDrawer } from '@/features/assistant/assistant-page/components/ParticipantDrawer';
import { FactDataModal } from '@/features/assistant/assistant-page/components/FactDataModal';

export function AssistantPage(): JSX.Element {
  const currentUser = useAuthStore((s) => s.user);
  const { containerRef, containerHeight } = useContainerHeight();

  // ---- 页面级状态 ----
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [showcaseCollapsed, setShowcaseCollapsed] = useState(false);
  const [activeTab, setActiveTab] = useState<'private' | 'collaborative'>('private');
  const [isSending, setIsSending] = useState(false);

  // ---- 事实数据相关状态 ----
  const [factModalOpen, setFactModalOpen] = useState(false);
  const [selectedFactIds, setSelectedFactIds] = useState<string[]>([]);
  const [insertingFact, setInsertingFact] = useState(false);
  const [factContext, setFactContext] = useState<string | null>(null);
  const [factContextLabel, setFactContextLabel] = useState<string | null>(null);
  const [factSearchText, setFactSearchText] = useState('');
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  // ---- 协作相关状态 ----
  const [inviteModalOpen, setInviteModalOpen] = useState(false);
  const [participantDrawerOpen, setParticipantDrawerOpen] = useState(false);
  const [inviteUserIds, setInviteUserIds] = useState<string[]>([]);

  // ---- hooks: 查询（传入 isSending 控制 refetchInterval） ----
  const queries = useAssistantQueries({
    selectedConvId,
    isSending,
    factModalOpen,
    inviteModalOpen,
    showArchived,
    searchKeyword,
    activeTab,
  });

  // ---- hooks: 流式状态管理 ----
  const streaming = useStreamingAnswer({
    selectedConvId,
    setSelectedConvId,
    thinkingEnabled,
    factContext,
    setFactContext,
    setFactContextLabel,
    isCollaborative: queries.isCollaborative,
    conversationList: queries.conversationList,
    messagesData: queries.messagesData,
    isSending,
    setIsSending,
  });

  // ---- hooks: mutations ----
  const mutations = useAssistantMutations({
    selectedConvId,
    setSelectedConvId,
    setSelectedFactIds,
    selectedFactIds,
    setFactModalOpen,
    setInsertingFact,
    setFactContext,
    setFactContextLabel,
    setLocalMessages: streaming.setLocalMessages,
    setStreamingAnswer: streaming.setStreamingAnswer,
    factsData: queries.factsData,
  });

  // ---- 事实分组 ----
  const factGroups = useMemo(
    () => buildFactGroups(queries.factsData?.items ?? [], factSearchText),
    [queries.factsData, factSearchText],
  );

  const allFilteredFactIds = useMemo(
    () => flattenFactIds(factGroups),
    [factGroups],
  );

  const allSelected = allFilteredFactIds.length > 0 && allFilteredFactIds.every((id) => selectedFactIds.includes(id));
  const someSelected = allFilteredFactIds.some((id) => selectedFactIds.includes(id));

  // ---- 合并显示消息 ----
  const displayMessages = useMemo(
    () => mergeDisplayMessages(
      queries.messagesData ?? [],
      streaming.localMessages,
      streaming.streamingAnswer,
      selectedConvId,
    ),
    [queries.messagesData, streaming.localMessages, streaming.streamingAnswer, selectedConvId],
  );

  // ---- 自动滚动到底部 ----
  useEffect(() => {
    streaming.messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [displayMessages, streaming.streamingAnswer, streaming.messagesEndRef]);

  // ---- 选中对话时自动选中最新对话（保留原有空逻辑） ----
  useEffect(() => {
    if (!selectedConvId && queries.conversationList.length > 0) {
      // 自动选中第一个对话（最新的）
      // 对话列表按时间降序，第一个就是最新的
      // 不自动选中，让用户选择或新建
    }
  }, [queries.conversationList, selectedConvId]);

  // ---- 打开邀请 Modal 时预选当前参与者 ----
  const handleOpenInviteModal = (): void => {
    setInviteUserIds((queries.participantsData ?? []).filter((p) => p.role !== 'owner').map((p) => p.user_id));
    setInviteModalOpen(true);
  };

  // ---- InviteModal 确认 ----
  const handleInviteOk = async (): Promise<void> => {
    await mutations.handleInviteModalOk(inviteUserIds, queries.participantsData);
    setInviteModalOpen(false);
  };

  return (
    <div ref={containerRef} style={{ display: 'flex', gap: 16, height: containerHeight, overflow: 'hidden' }}>
      <style>{`
        .ant-list-item:hover .conv-actions {
          opacity: 1 !important;
        }
      `}</style>
      {/* ---- 左侧：对话列表 + 搜索 ---- */}
      <ConversationSidebar
        showArchived={showArchived}
        setShowArchived={setShowArchived}
        setSearchKeyword={setSearchKeyword}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        conversationList={queries.conversationList}
        selectedConvId={selectedConvId}
        setSelectedConvId={setSelectedConvId}
        onNewConversation={() => void mutations.handleNewConversation()}
        onTogglePin={mutations.handleTogglePin}
        onToggleArchive={mutations.handleToggleArchive}
        onDeleteConversation={mutations.handleDeleteConversation}
      />

      {/* ---- 右侧：对话区域 ---- */}
      <MessageList
        displayMessages={displayMessages}
        isSending={streaming.isSending}
        streamingAnswer={streaming.streamingAnswer}
        selectedConvId={selectedConvId}
        factContext={factContext}
        messagesEndRef={streaming.messagesEndRef}
        aiOnline={queries.aiOnline}
        participantsData={queries.participantsData}
        isOwner={queries.isOwner}
        onOpenInviteModal={handleOpenInviteModal}
        onOpenParticipantDrawer={() => setParticipantDrawerOpen(true)}
      >
        {/* 输入区域（渲染在 MessageList Card 内部底部） */}
        <MessageInputArea
          inputText={streaming.inputText}
          setInputText={streaming.setInputText}
          mentions={streaming.mentions}
          setMentions={streaming.setMentions}
          isSending={streaming.isSending}
          thinkingEnabled={thinkingEnabled}
          setThinkingEnabled={setThinkingEnabled}
          factContext={factContext}
          factContextLabel={factContextLabel}
          onOpenFactModal={() => setFactModalOpen(true)}
          onClearFactContext={mutations.handleClearFactContext}
          onSend={streaming.handleSend}
          onCancelRequest={streaming.handleCancelRequest}
          isCollaborative={queries.isCollaborative}
          participantsData={queries.participantsData}
        />
      </MessageList>

      {/* irip-ai-collab: 成员管理 Modal（邀请 + 移除一体） */}
      <InviteModal
        open={inviteModalOpen}
        selectedConvId={selectedConvId}
        inviteUserIds={inviteUserIds}
        setInviteUserIds={setInviteUserIds}
        mentionableUsersData={queries.mentionableUsersData}
        currentUser={currentUser}
        onOk={handleInviteOk}
        onCancel={() => { setInviteModalOpen(false); setInviteUserIds([]); }}
      />

      {/* irip-ai-collab: 参与者列表 Drawer */}
      <ParticipantDrawer
        open={participantDrawerOpen}
        onClose={() => setParticipantDrawerOpen(false)}
        participantsData={queries.participantsData}
        isOwner={queries.isOwner}
        selectedConvId={selectedConvId}
        onRemoveParticipant={mutations.handleRemoveParticipant}
      />

      {/* ---- 右侧：分析橱窗 ---- */}
      <ShowcasePanel
        conversationId={selectedConvId}
        conversationTitle={queries.conversationList.find((c) => c.id === selectedConvId)?.title ?? ''}
        collapsed={showcaseCollapsed}
        onToggleCollapse={() => setShowcaseCollapsed(!showcaseCollapsed)}
        onLocateMessage={mutations.handleLocateMessage}
      />

      {/* 载入实验数据 Modal */}
      <FactDataModal
        open={factModalOpen}
        insertingFact={insertingFact}
        factSearchText={factSearchText}
        setFactSearchText={setFactSearchText}
        factGroups={factGroups}
        expandedGroups={expandedGroups}
        setExpandedGroups={setExpandedGroups}
        selectedFactIds={selectedFactIds}
        allFilteredFactIds={allFilteredFactIds}
        allSelected={allSelected}
        someSelected={someSelected}
        onSelectAll={() => mutations.handleSelectAll(allFilteredFactIds)}
        onToggleFact={mutations.handleToggleFact}
        onToggleGroup={mutations.handleToggleGroup}
        onOk={() => void mutations.handleInsertFact()}
        onCancel={() => { setFactModalOpen(false); setFactSearchText(''); }}
      />
    </div>
  );
}

export default AssistantPage;
