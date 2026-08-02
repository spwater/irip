import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import {
  Avatar,
  Button,
  Card,
  Checkbox,
  Drawer,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { SearchOutlined, UserAddOutlined, TeamOutlined } from '@ant-design/icons';
import {
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import MessageThread from '@/features/assistant/MessageThread';
import ConversationSearch from '@/features/assistant/ConversationSearch';
import ShowcasePanel from '@/features/assistant/ShowcasePanel';
import ConversationTabs from '@/features/assistant/ConversationTabs';
import MentionInput from '@/features/assistant/MentionInput';
import {
  apiCancelRequest,
  apiCreateConversation,
  apiDeleteConversation,
  apiGetProviderStatus,
  apiListConversations,
  apiListMessages,
  apiSendMessage,
  apiTogglePin,
  apiToggleArchive,
  type AssistantMessage,
  type ConversationSummary,
} from '@/api/models-ai';
import { apiListParticipants, apiInviteParticipant, apiRemoveParticipant, apiListMentionableUsers } from '@/api/collaboration';
import { apiListFacts, apiGetFactData } from '@/api/facts-provenance';
import { extractApiError, type FactSummary } from '@/api/types';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { compactJson } from '@/shared/json-utils';

const { Title, Text } = Typography;

/**
 * AI 助手主页面
 *
 * 改进：
 * 1. 进入页面直接可对话，首次发言自动创建对话，标题根据首条消息自动生成
 * 2. 对话消息持久化，刷新页面后历史消息保留
 * 3. 用户消息立即显示，AI 回答逐字流式输出
 */
export function AssistantPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [inputText, setInputText] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [factModalOpen, setFactModalOpen] = useState(false);
  const [selectedFactIds, setSelectedFactIds] = useState<string[]>([]);
  const [insertingFact, setInsertingFact] = useState(false);
  const [factContext, setFactContext] = useState<string | null>(null);
  const [factContextLabel, setFactContextLabel] = useState<string | null>(null);
  const [factSearchText, setFactSearchText] = useState('');
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [inviteModalOpen, setInviteModalOpen] = useState(false);
  const [participantDrawerOpen, setParticipantDrawerOpen] = useState(false);
  const [inviteUserIds, setInviteUserIds] = useState<string[]>([]);

  // 搜索关键词 + 橱窗收起状态
  const [searchKeyword, setSearchKeyword] = useState('');
  const [showcaseCollapsed, setShowcaseCollapsed] = useState(false);

  // irip-ai-collab: 协作功能状态
  const currentUser = useAuthStore((s) => s.user);
  const [activeTab, setActiveTab] = useState<'private' | 'collaborative'>('private');
  const [mentions, setMentions] = useState<string[]>([]);

  // 查询事实列表（用于插入实验数据）
  const { data: factsData } = useQuery({
    queryKey: ['facts-for-insert'],
    queryFn: () => apiListFacts({ page_size: 100 }),
    enabled: factModalOpen,
  });

  // 按任务分组样品，支持搜索过滤
  const factGroups = useMemo(() => {
    const allFacts = factsData?.items ?? [];
    const filtered = factSearchText.trim()
      ? allFacts.filter((f) =>
          f.subject_id.toLowerCase().includes(factSearchText.toLowerCase()) ||
          (f.task_name ?? '').toLowerCase().includes(factSearchText.toLowerCase())
        )
      : allFacts;
    const groups: Record<string, { taskName: string; facts: FactSummary[] }> = {};
    for (const f of filtered) {
      const key = f.task_code ?? '未分组';
      if (!groups[key]) groups[key] = { taskName: f.task_name ?? f.task_code ?? '未分组', facts: [] };
      groups[key].facts.push(f);
    }
    return groups;
  }, [factsData, factSearchText]);

  const allFilteredFactIds = useMemo(() => {
    return Object.values(factGroups).flatMap((g) => g.facts.map((f) => f.fact_id));
  }, [factGroups]);

  const allSelected = allFilteredFactIds.length > 0 && allFilteredFactIds.every((id) => selectedFactIds.includes(id));
  const someSelected = allFilteredFactIds.some((id) => selectedFactIds.includes(id));

  const handleSelectAll = () => {
    if (allSelected) {
      setSelectedFactIds((prev) => prev.filter((id) => !allFilteredFactIds.includes(id)));
    } else {
      setSelectedFactIds((prev) => Array.from(new Set([...prev, ...allFilteredFactIds])));
    }
  };

  const handleToggleFact = (factId: string) => {
    setSelectedFactIds((prev) =>
      prev.includes(factId) ? prev.filter((id) => id !== factId) : [...prev, factId]
    );
  };

  const handleToggleGroup = (groupFactIds: string[]) => {
    const allInGroup = groupFactIds.every((id) => selectedFactIds.includes(id));
    if (allInGroup) {
      setSelectedFactIds((prev) => prev.filter((id) => !groupFactIds.includes(id)));
    } else {
      setSelectedFactIds((prev) => Array.from(new Set([...prev, ...groupFactIds])));
    }
  };

  // 本地消息缓存：用户消息立即显示 + AI 回答流式追加
  const [localMessages, setLocalMessages] = useState<AssistantMessage[]>([]);
  // 流式回答的临时内容
  const [streamingAnswer, setStreamingAnswer] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const streamingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

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

  // ---- 选中对话时自动选中最新对话 ----
  useEffect(() => {
    if (!selectedConvId && conversationList.length > 0) {
      // 自动选中第一个对话（最新的）
      // 对话列表按时间降序，第一个就是最新的
      // 不自动选中，让用户选择或新建
    }
  }, [conversationList, selectedConvId]);

  // ---- 消息列表查询（依赖选中对话） ----
  const { data: messagesData } = useQuery({
    queryKey: ['assistant-messages', selectedConvId],
    queryFn: () => apiListMessages(selectedConvId!),
    enabled: !!selectedConvId,
    retry: false,
    refetchInterval: selectedConvId && !isSending ? 3_000 : false, // 发送/流式期间暂停轮询，避免重复 AI 消息
  });

  // irip-ai-collab: 查询参与者（判断当前用户是否为 owner）
  const { data: participantsData } = useQuery({
    queryKey: ['participants', selectedConvId],
    queryFn: () => apiListParticipants(selectedConvId!),
    enabled: !!selectedConvId,
    retry: false,
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

  // 合并本地消息和数据库消息
  const displayMessages: AssistantMessage[] = (() => {
    const dbMessages = messagesData ?? [];
    const newLocalMessages = localMessages;

    if (streamingAnswer !== null) {
      // 流式输出中：显示历史消息 + 本地用户消息 + 流式 AI 消息
      // 过滤掉 DB 中与本地消息重复的（同 id），保留所有历史 AI 消息
      const localIds = new Set(newLocalMessages.map((m) => m.id));
      const dbHistory = dbMessages.filter((m) => !localIds.has(m.id));
      const aiMsg: AssistantMessage = {
        id: 'streaming-ai',
        conversation_id: selectedConvId ?? '',
        role: 'assistant',
        content: streamingAnswer,
        tool_calls: [],
        citations: [],
        uncertainty: null,
        created_at: new Date().toISOString(),
        mentions: [],
        sender_user_id: null,
        sender_display_name: null,
        sender_avatar_url: null,
      };
      return [...dbHistory, ...newLocalMessages, aiMsg];
    }
    // 非流式：合并数据库消息和本地消息（避免重复）
    if (newLocalMessages.length > 0 && dbMessages.length === 0) {
      return newLocalMessages;
    }
    return dbMessages;
  })();

  // ---- 自动滚动到底部 ----
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [displayMessages, streamingAnswer]);

  // ---- 插入实验数据 ----
  const handleInsertFact = async (): Promise<void> => {
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
  };

  const handleClearFactContext = (): void => {
    setFactContext(null);
    setFactContextLabel(null);
    setSelectedFactIds([]);
  };

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

  // ---- 发送消息 ----
  const handleSend = useCallback(async (): Promise<void> => {
    const trimmed = inputText.trim();
    if (!trimmed || isSending) return;

    let convId = selectedConvId;

    // 保存当前 mentions 引用（setMentions 会异步清空）
    const currentMentions = [...mentions];

    // 立即显示用户消息（在创建对话之前就显示，避免首次提问等待期间看不到）
    const userMsg: AssistantMessage = {
      id: `local-${Date.now()}`,
      conversation_id: convId ?? 'pending',
      role: 'user',
      content: trimmed,
      tool_calls: [],
      citations: [],
      uncertainty: null,
      created_at: new Date().toISOString(),
      mentions: currentMentions,
      sender_user_id: currentUser?.id ?? null,
      sender_display_name: currentUser?.displayName ?? null,
      sender_avatar_url: currentUser?.avatarUrl ?? null,
    };
    setLocalMessages((prev) => [...prev, userMsg]);
    setInputText('');
    setMentions([]); // 清空 mentions
    setIsSending(true);

    // irip-ai-collab: 根据对话类型判断是否触发 AI 回复
    // 私有对话（参与者 <= 1）：AI 自动回复（不管 mentions）
    // 协作对话（参与者 > 1）：mentions 中包含 "ai" 才触发 AI，否则只保存用户消息
    const isMentionOnly = isCollaborative && !currentMentions.includes('ai');

    // 如果没有选中对话，自动创建一个
    if (!convId) {
      try {
        const conv = await apiCreateConversation({
          title: trimmed.slice(0, 30),
          provider_mode: 'openai_compatible',
        });
        convId = conv.id;
        setSelectedConvId(conv.id);
        void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
      } catch (err) {
        setLocalMessages([]);
        setStreamingAnswer(null);
        setIsSending(false);
        message.error(extractApiError(err));
        return;
      }
    }

    // 协作对话中仅 @人（不 @AI）模式：仅保存用户消息，不显示 AI 流式回复
    if (isMentionOnly) {
      // 不等待后端返回，立即解锁输入框（用户消息已通过 localMessages 显示）
      setIsSending(false);
      try {
        await apiSendMessage(convId, {
          question: trimmed,
          thinking_enabled: thinkingEnabled,
          system_context: factContext ?? undefined,
          mentions: currentMentions,
        });
      } catch (err) {
        message.error(extractApiError(err));
      }
      // 刷新数据库消息（无论成功失败都刷新，确保消息持久化）
      void queryClient.invalidateQueries({ queryKey: ['assistant-messages', convId] });
      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
      setLocalMessages([]);
      return;
    }

    // 正常 AI 问答模式（私有对话或协作对话中 @AI）
    setStreamingAnswer('');

    // 创建 AbortController 用于中断请求
    abortControllerRef.current = new AbortController();

    // 模拟流式输出（逐字显示）
    // 实际 API 返回完整回答后，用定时器逐字追加
    try {
      const res = await apiSendMessage(convId, { question: trimmed, thinking_enabled: thinkingEnabled, system_context: factContext ?? undefined, mentions: currentMentions.length > 0 ? currentMentions : undefined }, abortControllerRef.current.signal);
      const fullAnswer = res.answer || '(无回答)';

      // 逐字流式显示
      let charIndex = 0;
      if (streamingTimerRef.current) {
        clearInterval(streamingTimerRef.current);
      }
      streamingTimerRef.current = setInterval(() => {
        charIndex += 3; // 每次显示 3 个字符
        if (charIndex >= fullAnswer.length) {
          if (streamingTimerRef.current) {
            clearInterval(streamingTimerRef.current);
            streamingTimerRef.current = null;
          }
          setStreamingAnswer(fullAnswer);
          // 流式结束后，刷新数据库消息（不清空 localMessages，等 DB 数据到达后由 useEffect 清）
          setTimeout(() => {
            // 先 invalidate 拉取 DB 消息，等数据到达后再清 streamingAnswer
            void queryClient.invalidateQueries({ queryKey: ['assistant-messages', convId] });
            void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
            // 延迟 100ms 让 DB 消息先到达，再清 streamingAnswer，避免中间空白闪烁
            setTimeout(() => {
              setStreamingAnswer(null);
              setIsSending(false);
            }, 100);
          }, 300);
        } else {
          setStreamingAnswer(fullAnswer.slice(0, charIndex));
        }
      }, 20); // 每 20ms 显示 3 个字符
    } catch (err) {
      // 如果是用户主动中断，不报错
      if (err instanceof DOMException && err.name === 'AbortError') {
        // 中断：保留已显示的内容，不报错
      } else if (abortControllerRef.current?.signal.aborted) {
        // 中断后的 reject
      } else {
        setStreamingAnswer(null);
        message.error(extractApiError(err));
      }
      setIsSending(false);
      abortControllerRef.current = null;
      // 刷新数据库消息（可能用户消息已保存但 AI 回答失败）
      void queryClient.invalidateQueries({ queryKey: ['assistant-messages', convId] });
    }
  }, [inputText, isSending, selectedConvId, thinkingEnabled, factContext, queryClient, mentions, currentUser, isCollaborative]);

  // 清理定时器
  useEffect(() => {
    return () => {
      if (streamingTimerRef.current) {
        clearInterval(streamingTimerRef.current);
      }
    };
  }, []);

  // 切换对话时清空本地消息，恢复该对话关联的实验数据上下文
  // 注意：依赖项含 conversationList，确保对话列表加载完成后也能恢复 system_context
  useEffect(() => {
    if (!isSending) {
      setLocalMessages([]);
      setStreamingAnswer(null);
      // 从对话列表里找到选中的对话，恢复其 system_context
      const conv = conversationList.find((c) => c.id === selectedConvId);
      if (conv?.system_context) {
        setFactContext(conv.system_context);
        // 从 system_context 里提取样品标签
        const labels = (conv.system_context.match(/### 样品: (.+)/g) || [])
          .map((s) => s.replace('### 样品: ', ''));
        setFactContextLabel(labels.length > 0 ? labels.join(', ') : '已加载');
      } else {
        setFactContext(null);
        setFactContextLabel(null);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedConvId, conversationList]);

  // 数据库消息到达后清空本地消息（避免重复显示）
  useEffect(() => {
    if (messagesData && messagesData.length > 0 && !isSending && streamingAnswer === null) {
      setLocalMessages([]);
    }
  }, [messagesData, isSending, streamingAnswer]);

  // 动态计算可用高度：100vh - header - content padding - contentframe padding
  // 避免硬编码 180px 在 header 高度变化时不准
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerHeight, setContainerHeight] = useState('calc(100vh - 180px)');
  useEffect(() => {
    const updateHeight = () => {
      const el = containerRef.current;
      if (!el) return;
      // 找到最近的 scrollable 祖先（Content 区域）
      const rect = el.getBoundingClientRect();
      const available = window.innerHeight - rect.top - 24; // 24px = ContentFrame padding-bottom
      setContainerHeight(`${available}px`);
    };
    updateHeight();
    window.addEventListener('resize', updateHeight);
    // 延迟一次，等 header 渲染完成
    const timer = setTimeout(updateHeight, 200);
    return () => {
      window.removeEventListener('resize', updateHeight);
      clearTimeout(timer);
    };
  }, []);

  return (
    <div ref={containerRef} style={{ display: 'flex', gap: 16, height: containerHeight, overflow: 'hidden' }}>
      <style>{`
        .ant-list-item:hover .conv-actions {
          opacity: 1 !important;
        }
      `}</style>
      {/* ---- 左侧：对话列表 + 搜索 ---- */}
      <Card
        size="small"
        style={{ width: 260, display: 'flex', flexDirection: 'column', flexShrink: 0 }}
        styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' } }}
        title={
          <Space size={4}>
            <Title level={5} style={{ margin: 0 }}>
              {showArchived ? '归档对话' : '对话列表'}
            </Title>
            <Button
              type="link"
              size="small"
              style={{ padding: '0 4px', fontSize: 12 }}
              onClick={() => setShowArchived(!showArchived)}
            >
              {showArchived ? '返回' : '归档'}
            </Button>
          </Space>
        }
        extra={
          showArchived ? null : (
          <Button
            type="primary"
            size="small"
            onClick={async () => {
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
            }}
          >
            新建
          </Button>
          )
        }
      >
        <ConversationSearch onSearch={setSearchKeyword} />
        <ConversationTabs activeTab={activeTab} onTabChange={setActiveTab} />
        <div style={{ flex: 1, overflow: 'auto' }}>
        <List
          dataSource={conversationList}
          renderItem={(conv: ConversationSummary) => (
            <List.Item
              style={{
                padding: '8px 12px',
                cursor: 'pointer',
                position: 'relative',
                background: selectedConvId === conv.id ? 'rgba(22, 134, 174, 0.12)' : 'transparent',
                transition: 'background 0.2s',
              }}
              role="button"
              tabIndex={0}
              aria-label={`选择对话：${conv.title || '新对话'}`}
              aria-pressed={selectedConvId === conv.id}
              onClick={() => setSelectedConvId(conv.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setSelectedConvId(conv.id);
                }
              }}
            >
              <List.Item.Meta
                title={
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontWeight: selectedConvId === conv.id ? 600 : 400, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                      {conv.title || '新对话'}
                    </span>
                    <div style={{ display: 'flex', gap: 2, flexShrink: 0, marginLeft: 4 }}>
                      {conv.pinned && <Tag color='gold' style={{ fontSize: 9, margin: 0, padding: '0 4px', lineHeight: '16px' }}>置顶</Tag>}
                      {conv.archived && <Tag color='default' style={{ fontSize: 9, margin: 0, padding: '0 4px', lineHeight: '16px' }}>归档</Tag>}
                    </div>
                  </div>
                }
                description={
                  conv.participants && conv.participants.length > 1 ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                      <Avatar.Group size={20} maxCount={3}>
                        {conv.participants.slice(0, 3).map((p) => (
                          <Avatar
                            key={p.user_id}
                            src={p.avatar_url}
                            size={20}
                            style={{ fontSize: 10 }}
                          >
                            {p.display_name?.charAt(0) || '?'}
                          </Avatar>
                        ))}
                      </Avatar.Group>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {conv.participants.length}人协作
                      </Text>
                    </div>
                  ) : undefined
                }
              />
              {/* hover 悬浮操作按钮 */}
              <div
                className="conv-actions"
                style={{
                  position: 'absolute',
                  right: 6,
                  top: 6,
                  display: 'flex',
                  gap: 2,
                  opacity: 0,
                  transition: 'opacity 0.2s',
                  background: selectedConvId === conv.id ? 'rgba(22, 134, 174, 0.12)' : 'var(--ocean-surface-strong)',
                  padding: '2px 4px',
                  borderRadius: 4,
                  boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
                }}
              >
                <Button
                  size="small"
                  type="text"
                  onClick={(e) => {
                    e.stopPropagation();
                    apiTogglePin(conv.id).then(() => {
                      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
                    }).catch((err) => message.error(extractApiError(err)));
                  }}
                  style={{ padding: '0 4px', fontSize: 12 }}
                >
                  {conv.pinned ? '解除置顶' : '置顶'}
                </Button>
                <Button
                  size="small"
                  type="text"
                  onClick={(e) => {
                    e.stopPropagation();
                    apiToggleArchive(conv.id).then(() => {
                      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
                      if (selectedConvId === conv.id) {
                        setSelectedConvId(null);
                      }
                    }).catch((err) => message.error(extractApiError(err)));
                  }}
                  style={{ padding: '0 4px', fontSize: 12 }}
                >
                  {conv.archived ? '取消归档' : '归档'}
                </Button>
                {showArchived && (
                  <Popconfirm
                    title="确认永久删除？"
                    description="删除后无法恢复该对话及其所有消息。"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => {
                      apiDeleteConversation(conv.id).then(() => {
                        void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
                        if (selectedConvId === conv.id) {
                          setSelectedConvId(null);
                          setLocalMessages([]);
                          setStreamingAnswer(null);
                          void queryClient.removeQueries({ queryKey: ['assistant-messages', conv.id] });
                          void queryClient.removeQueries({ queryKey: ['participants', conv.id] });
                          void queryClient.removeQueries({ queryKey: ['showcase-items', conv.id] });
                        }
                        message.success('对话已删除');
                      }).catch((err) => message.error(extractApiError(err)));
                    }}
                  >
                    <Button
                      size="small"
                      type="text"
                      danger
                      onClick={(e) => e.stopPropagation()}
                      style={{ padding: '0 4px', fontSize: 12, color: 'var(--ocean-status-danger)' }}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                )}
              </div>
            </List.Item>
          )}
          locale={{ emptyText: showArchived ? '暂无归档对话' : '暂无对话，直接输入消息即可开始' }}
        />
        </div>
      </Card>

      {/* ---- 右侧：对话区域 ---- */}
      <Card
        size="small"
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        styles={{
          body: {
            padding: 16,
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          },
        }}
      >
        {/* irip-ai-collab: 参与者工具栏 */}
        {selectedConvId && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, paddingBottom: 8, borderBottom: '1px solid #f0f0f0' }}>
            <Space size="small">
              {/* AI 助手状态指示 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginRight: 4 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    backgroundColor: aiOnline ? '#52c41a' : '#bfbfbf',
                    boxShadow: aiOnline ? '0 0 4px rgba(82,196,26,0.5)' : 'none',
                    flexShrink: 0,
                  }}
                />
                <Text style={{ fontSize: 13, color: aiOnline ? 'inherit' : 'var(--ocean-text-muted, #999)' }}>
                  中材小艾
                </Text>
              </div>
              {(participantsData ?? []).length > 1 && (
                <>
                  <span style={{ color: '#d9d9d9', fontSize: 12 }}>|</span>
                  <Avatar.Group size="small" maxCount={5}>
                    {(participantsData ?? []).map((p) => (
                      <Avatar key={p.user_id} src={p.avatar_url} size={24} style={{ backgroundColor: p.role === 'owner' ? '#faad14' : '#1686AE', fontSize: 11 }}>
                        {p.display_name.charAt(0)}
                      </Avatar>
                    ))}
                  </Avatar.Group>
                  <Button size="small" type="text" icon={<TeamOutlined />} onClick={() => setParticipantDrawerOpen(true)}>
                    {(participantsData ?? []).length}人
                  </Button>
                </>
              )}
            </Space>
            {isOwner && (
              <Button size="small" type="primary" ghost icon={<UserAddOutlined />} onClick={() => {
                // 打开时预选当前参与者（排除 owner 自己）
                setInviteUserIds((participantsData ?? []).filter((p) => p.role !== 'owner').map((p) => p.user_id));
                setInviteModalOpen(true);
              }}>
                邀请成员
              </Button>
            )}
          </div>
        )}
        {/* 消息列表区域 */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '8px 4px',
            background: 'rgba(232, 246, 249, 0.5)',
            borderRadius: 8,
            marginBottom: 12,
          }}
        >
          {displayMessages.length === 0 && !isSending ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
              }}
            >
              <Text type="secondary">
                直接输入问题开始对话，无需新建
              </Text>
            </div>
          ) : (
            <>
              <MessageThread
                messages={displayMessages}
                conversationId={selectedConvId}
                systemContext={factContext}
              />
              {isSending && streamingAnswer === '' && (
                <div style={{ padding: '8px 16px' }}>
                  <Text type="secondary">AI 正在回复...</Text>
                </div>
              )}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* 输入区域 */}
        <div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
            <Tooltip title="开启后 AI 会先思考再回答（适用于 Qwen3 等支持思考模式的模型）">
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, paddingBottom: 8, flexShrink: 0 }}>
                <Switch
                  size="small"
                  checked={thinkingEnabled}
                  onChange={setThinkingEnabled}
                />
                <Text type="secondary" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                  思考
                </Text>
              </div>
            </Tooltip>
            <Button
              type={factContext ? 'primary' : 'default'}
              onClick={() => setFactModalOpen(true)}
              style={{ flexShrink: 0, height: 'auto', minHeight: 32, alignSelf: 'stretch' }}
            >
              {factContext ? '📊 数据已加载' : '载入实验数据'}
            </Button>
            {factContext && (
              <Tooltip title={`已加载: ${factContextLabel}（点击清除）`}>
                <Button
                  type="link"
                  danger
                  onClick={handleClearFactContext}
                  style={{ flexShrink: 0, padding: '0 4px', height: 'auto', minHeight: 32, alignSelf: 'stretch' }}
                >
                  ✕
                </Button>
              </Tooltip>
            )}
            <MentionInput
              value={inputText}
              onChange={setInputText}
              mentions={mentions}
              onMentionsChange={setMentions}
              placeholder="输入问题，@ 提及成员，Enter 发送"
              disabled={isSending}
              isCollaborative={isCollaborative}
              participants={participantsData ?? []}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              style={{ flex: 1 }}
            />
          </div>
          <div
            style={{
              marginTop: 8,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <Text type="secondary" style={{ fontSize: 11 }}>
              Enter 发送 · Shift+Enter 换行
            </Text>
            <Space>
              <Button
                type="primary"
                onClick={() => void handleSend()}
                loading={isSending}
                disabled={!inputText.trim()}
              >
                发送
              </Button>
              {isSending && (
                <Button
                  danger
                  onClick={() => {
                    // 1. 通知后端取消 AI 请求
                    if (selectedConvId) {
                      void apiCancelRequest(selectedConvId);
                    }
                    // 2. 中断前端 HTTP 请求
                    if (abortControllerRef.current) {
                      abortControllerRef.current.abort();
                      abortControllerRef.current = null;
                    }
                    // 3. 清除流式定时器
                    if (streamingTimerRef.current) {
                      clearInterval(streamingTimerRef.current);
                      streamingTimerRef.current = null;
                    }
                    // 4. 重置状态
                    setStreamingAnswer(null);
                    setIsSending(false);
                    void queryClient.invalidateQueries({ queryKey: ['assistant-messages', selectedConvId] });
                  }}
                  style={{ fontWeight: 700 }}
                  title="中断对话"
                >
                  ■
                </Button>
              )}
            </Space>
          </div>
        </div>
      </Card>

      {/* irip-ai-collab: 成员管理 Modal（邀请 + 移除一体） */}
      <Modal
        title="管理对话成员"
        open={inviteModalOpen}
        onOk={async () => {
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
          setInviteModalOpen(false);
          if (okCount > 0 && failCount === 0) message.success(`操作成功（${okCount} 项变更）`);
          else if (failCount > 0) message.warning(`${okCount} 项成功，${failCount} 项失败`);
          else message.info('无变更');
        }}
        onCancel={() => { setInviteModalOpen(false); setInviteUserIds([]); }}
        okText="保存"
        cancelText="取消"
        width={480}
      >
        <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
          勾选要加入的成员，取消勾选移除成员。仅限同组织活跃用户。
        </Text>
        <Select
          mode="multiple"
          placeholder="选择成员"
          style={{ width: '100%' }}
          value={inviteUserIds}
          onChange={setInviteUserIds}
          showSearch
          optionFilterProp="label"
          options={(mentionableUsersData ?? [])
            .filter((u) => u.id !== currentUser?.id)
            .map((u) => ({ value: u.id, label: `${u.display_name}${u.roles.length > 0 ? ` (${u.roles.join(', ')})` : ''}` }))}
          notFoundContent="无可选用户"
        />
      </Modal>

      {/* irip-ai-collab: 参与者列表 Drawer */}
      <Drawer
        title="对话参与者"
        open={participantDrawerOpen}
        onClose={() => setParticipantDrawerOpen(false)}
        width={320}
      >
        <List
          dataSource={participantsData ?? []}
          renderItem={(p) => (
            <List.Item
              actions={
                isOwner && p.role !== 'owner'
                  ? [<Button key="remove" type="link" danger size="small"
                      onClick={async () => {
                        if (!selectedConvId) return;
                        try { await apiRemoveParticipant(selectedConvId, p.user_id);
                          void queryClient.invalidateQueries({ queryKey: ['participants', selectedConvId] });
                          message.success('成员已移除');
                        } catch (err) { message.error(extractApiError(err)); }
                      }}>移除</Button>]
                  : undefined
              }
            >
              <List.Item.Meta
                avatar={<Avatar src={p.avatar_url} style={{ backgroundColor: p.role === 'owner' ? '#faad14' : '#1686AE' }}>{p.display_name.charAt(0)}</Avatar>}
                title={<Space size={4}><Text>{p.display_name}</Text><Tag color={p.role === 'owner' ? 'gold' : 'blue'} style={{ fontSize: 10 }}>{p.role === 'owner' ? '创建者' : '成员'}</Tag></Space>}
                description={<Text type="secondary" style={{ fontSize: 12 }}>加入于 {new Date(p.joined_at).toLocaleDateString('zh-CN')}</Text>}
              />
            </List.Item>
          )}
          locale={{ emptyText: '暂无参与者' }}
        />
      </Drawer>

      {/* ---- 右侧：分析橱窗 ---- */}
      <ShowcasePanel
        conversationId={selectedConvId}
        conversationTitle={conversationList.find((c) => c.id === selectedConvId)?.title ?? ''}
        collapsed={showcaseCollapsed}
        onToggleCollapse={() => setShowcaseCollapsed(!showcaseCollapsed)}
        onLocateMessage={handleLocateMessage}
      />

      {/* 载入实验数据 Modal */}
      <Modal
        title="载入实验数据"
        open={factModalOpen}
        onOk={handleInsertFact}
        onCancel={() => { setFactModalOpen(false); setFactSearchText(''); }}
        confirmLoading={insertingFact}
        okText={`载入 ${selectedFactIds.length > 0 ? `(${selectedFactIds.length})` : ''}`}
        cancelText="取消"
        width={700}
        styles={{ body: { padding: 0 } }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 280px)' }}>
          {/* 搜索栏 */}
          <div style={{ padding: '12px 16px 8px', borderBottom: '1px solid var(--ocean-border-subtle)' }}>
            <Input
              prefix={<SearchOutlined />}
              placeholder="搜索样品名称或任务名称..."
              value={factSearchText}
              onChange={(e) => setFactSearchText(e.target.value)}
              allowClear
              size="middle"
            />
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
              <Checkbox
                checked={allSelected}
                indeterminate={!allSelected && someSelected}
                onChange={handleSelectAll}
              >
                全选 ({allFilteredFactIds.length} 个样品)
              </Checkbox>
              <Text type="secondary" style={{ fontSize: 12 }}>
                已选 {selectedFactIds.length} 个
              </Text>
            </div>
          </div>

          {/* 分组列表 */}
          <div style={{ flex: 1, overflow: 'auto', padding: '8px 16px' }}>
            {Object.keys(factGroups).length === 0 ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--ocean-text-muted)' }}>
                <Text type="secondary">暂无数据或未找到匹配的样品</Text>
              </div>
            ) : (
              Object.entries(factGroups).map(([taskCode, group]) => {
                const groupIds = group.facts.map((f) => f.fact_id);
                const groupAllSelected = groupIds.every((id) => selectedFactIds.includes(id));
                const groupSomeSelected = groupIds.some((id) => selectedFactIds.includes(id));
                // 搜索时自动展开，否则按 expandedGroups 状态
                const isExpanded = factSearchText.trim() || expandedGroups.has(taskCode);
                return (
                  <div key={taskCode} style={{ marginBottom: 4 }}>
                    {/* 任务分组标题 */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        padding: '8px 0',
                        cursor: 'pointer',
                        borderBottom: '1px solid var(--ocean-border-subtle)',
                        userSelect: 'none',
                      }}
                      onClick={() => {
                        setExpandedGroups((prev) => {
                          const next = new Set(prev);
                          if (next.has(taskCode)) next.delete(taskCode);
                          else next.add(taskCode);
                          return next;
                        });
                      }}
                    >
                      <span style={{ fontSize: 10, color: 'var(--ocean-text-muted)', width: 12, display: 'inline-block' }}>
                        {isExpanded ? '▼' : '▶'}
                      </span>
                      <Checkbox
                        checked={groupAllSelected}
                        indeterminate={!groupAllSelected && groupSomeSelected}
                        onChange={() => handleToggleGroup(groupIds)}
                        onClick={(e) => e.stopPropagation()}
                      />
                      <Text strong style={{ fontSize: 13 }}>{group.taskName}</Text>
                      <Tag style={{ fontSize: 10, margin: 0 }}>{group.facts.length}</Tag>
                      {groupSomeSelected && !groupAllSelected && (
                        <Tag color="blue" style={{ fontSize: 10, margin: 0 }}>
                          已选 {groupIds.filter((id) => selectedFactIds.includes(id)).length}
                        </Tag>
                      )}
                    </div>
                    {/* 样品列表 - 折叠时不渲染 */}
                    {isExpanded && (
                      <div style={{ paddingLeft: 28, paddingTop: 4 }}>
                        {group.facts.map((f) => (
                          <div
                            key={f.fact_id}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 8,
                              padding: '4px 0',
                              cursor: 'pointer',
                              borderRadius: 4,
                              background: selectedFactIds.includes(f.fact_id) ? 'rgba(22, 134, 174, 0.10)' : 'transparent',
                            }}
                            onClick={() => handleToggleFact(f.fact_id)}
                          >
                            <Checkbox
                              checked={selectedFactIds.includes(f.fact_id)}
                              onChange={() => handleToggleFact(f.fact_id)}
                              onClick={(e) => e.stopPropagation()}
                            />
                            <Text style={{ fontSize: 13, fontFamily: 'monospace' }}>{f.subject_id}</Text>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </Modal>
    </div>
  );
}

export default AssistantPage;
