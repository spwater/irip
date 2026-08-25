/**
 * AssistantPage 拆分模块共享类型定义。
 *
 * 从 AssistantPage.tsx 提取，供 hooks 和子组件使用。
 */

import type { CurrentUser } from '@/api/client';
import type { AssistantMessage, ConversationSummary } from '@/api/models-ai';
import type { Participant, MentionableUser } from '@/api/collaboration';
import type { FactSummary } from '@/api/types';

// ============================================================
// 查询 hooks 参数 / 返回
// ============================================================

export interface UseAssistantQueriesParams {
  selectedConvId: string | null;
  isSending: boolean;
  factModalOpen: boolean;
  inviteModalOpen: boolean;
  showArchived: boolean;
  searchKeyword: string;
  activeTab: 'private' | 'collaborative';
}

export interface UseAssistantQueriesResult {
  /** 对话列表 */
  conversationList: ConversationSummary[];
  /** 消息列表（DB） */
  messagesData: AssistantMessage[] | undefined;
  /** 参与者列表 */
  participantsData: Participant[] | undefined;
  /** 可邀请用户列表 */
  mentionableUsersData: MentionableUser[] | undefined;
  /** 事实列表 */
  factsData: { items: FactSummary[] } | undefined;
  /** AI Provider 是否在线 */
  aiOnline: boolean;
  /** 当前用户是否为选中对话的 owner */
  isOwner: boolean;
  /** 当前对话是否为协作对话 */
  isCollaborative: boolean;
}

// ============================================================
// 事实分组类型
// ============================================================

export type FactProjectGroup = {
  projectName: string;
  tasks: Record<string, { taskName: string; facts: FactSummary[] }>;
};

export type FactGroups = Record<string, FactProjectGroup>;

// ============================================================
// 流式状态 hook 返回
// ============================================================

export interface UseStreamingAnswerResult {
  localMessages: AssistantMessage[];
  streamingAnswer: string | null;
  isThinking: boolean;
  isSending: boolean;
  setInputText: (text: string) => void;
  inputText: string;
  setLocalMessages: React.Dispatch<React.SetStateAction<AssistantMessage[]>>;
  setStreamingAnswer: React.Dispatch<React.SetStateAction<string | null>>;
  setIsSending: (sending: boolean) => void;
  setMentions: (mentions: string[]) => void;
  mentions: string[];
  abortControllerRef: React.MutableRefObject<AbortController | null>;
  messagesEndRef: React.RefObject<HTMLDivElement>;
  /** 切换对话时恢复 system_context */
  restoreContextFromConversation: (conv: ConversationSummary | undefined) => void;
  /** 发送消息（含 SSE 流式逻辑） */
  handleSend: () => Promise<void>;
  /** 中断当前 AI 请求 */
  handleCancelRequest: () => void;
}

// ============================================================
// 子组件 Props
// ============================================================

export interface ConversationSidebarProps {
  showArchived: boolean;
  setShowArchived: (v: boolean) => void;
  setSearchKeyword: (v: string) => void;
  activeTab: 'private' | 'collaborative';
  setActiveTab: (v: 'private' | 'collaborative') => void;
  conversationList: ConversationSummary[];
  selectedConvId: string | null;
  setSelectedConvId: (id: string | null) => void;
  onNewConversation: () => void;
  onTogglePin: (convId: string) => void;
  onToggleArchive: (convId: string) => void;
  onDeleteConversation: (convId: string) => void;
}

export interface MessageListProps {
  displayMessages: AssistantMessage[];
  isSending: boolean;
  streamingAnswer: string | null;
  selectedConvId: string | null;
  factContext: string | null;
  messagesEndRef: React.RefObject<HTMLDivElement>;
}

export interface MessageInputAreaProps {
  inputText: string;
  setInputText: (text: string) => void;
  mentions: string[];
  setMentions: (m: string[]) => void;
  isSending: boolean;
  thinkingEnabled: boolean;
  setThinkingEnabled: (v: boolean) => void;
  factContext: string | null;
  factContextLabel: string | null;
  onOpenFactModal: () => void;
  onClearFactContext: () => void;
  onSend: () => void;
  onCancelRequest: () => void;
  isCollaborative: boolean;
  participantsData: Participant[] | undefined;
}

export interface InviteModalProps {
  open: boolean;
  selectedConvId: string | null;
  inviteUserIds: string[];
  setInviteUserIds: (v: string[]) => void;
  mentionableUsersData: MentionableUser[] | undefined;
  currentUser: CurrentUser | null;
  onOk: () => void;
  onCancel: () => void;
}

export interface ParticipantDrawerProps {
  open: boolean;
  onClose: () => void;
  participantsData: Participant[] | undefined;
  isOwner: boolean;
  selectedConvId: string | null;
  onRemoveParticipant: (convId: string, userId: string) => void;
}

export interface FactDataModalProps {
  open: boolean;
  insertingFact: boolean;
  factSearchText: string;
  setFactSearchText: (v: string) => void;
  factGroups: FactGroups;
  expandedGroups: Set<string>;
  setExpandedGroups: React.Dispatch<React.SetStateAction<Set<string>>>;
  selectedFactIds: string[];
  allFilteredFactIds: string[];
  allSelected: boolean;
  someSelected: boolean;
  onSelectAll: () => void;
  onToggleFact: (factId: string) => void;
  onToggleGroup: (groupFactIds: string[]) => void;
  onOk: () => void;
  onCancel: () => void;
}
