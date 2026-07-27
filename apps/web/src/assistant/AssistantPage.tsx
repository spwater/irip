import { useEffect, useRef, useState, useCallback } from 'react';
import {
  Button,
  Card,
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
import {
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import ProviderStatus from '@/assistant/ProviderStatus';
import MessageThread from '@/assistant/MessageThread';
import {
  apiCancelRequest,
  apiCreateConversation,
  apiDeleteConversation,
  apiListConversations,
  apiListMessages,
  apiSendMessage,
  apiTogglePin,
  apiToggleArchive,
  apiListFacts,
  apiGetFactData,
  extractApiError,
  type AssistantMessage,
  type ConversationSummary,
  type FactSummary,
} from '@/api/client';

const { Title, Text } = Typography;
const { TextArea } = Input;

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

  // 查询事实列表（用于插入实验数据）
  const { data: factsData } = useQuery({
    queryKey: ['facts-for-insert'],
    queryFn: () => apiListFacts({ page_size: 100 }),
    enabled: factModalOpen,
  });

  // 本地消息缓存：用户消息立即显示 + AI 回答流式追加
  const [localMessages, setLocalMessages] = useState<AssistantMessage[]>([]);
  // 流式回答的临时内容
  const [streamingAnswer, setStreamingAnswer] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const streamingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // ---- 对话列表查询 ----
  const { data: conversations } = useQuery({
    queryKey: ['assistant-conversations', showArchived],
    queryFn: () => apiListConversations({ archivedOnly: showArchived }),
    retry: false,
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
  });

  // 合并本地消息和数据库消息
  const displayMessages: AssistantMessage[] = (() => {
    const dbMessages = messagesData ?? [];
    const newLocalMessages = localMessages;

    if (streamingAnswer !== null) {
      // 流式输出中：历史消息 + 本地新消息 + 流式 AI 消息
      const aiMsg: AssistantMessage = {
        id: 'streaming-ai',
        conversation_id: selectedConvId ?? '',
        role: 'assistant',
        content: streamingAnswer,
        tool_calls: [],
        citations: [],
        uncertainty: null,
        created_at: new Date().toISOString(),
      };
      return [...dbMessages, ...newLocalMessages, aiMsg];
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
        allData.push(`### 样品: ${label}\n\`\`\`json\n${JSON.stringify(data, null, 2)}\n\`\`\``);
      }
      const context = `以下是实验数据，请基于此数据回答用户的问题：\n\n${allData.join('\n\n')}`;
      setFactContext(context);
      setFactContextLabel(labels.join(', '));
      setFactModalOpen(false);
      setSelectedFactIds([]);
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
  };

  // ---- 发送消息 ----
  const handleSend = useCallback(async (): Promise<void> => {
    const trimmed = inputText.trim();
    if (!trimmed || isSending) return;

    let convId = selectedConvId;

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
    };
    setLocalMessages((prev) => [...prev, userMsg]);
    setInputText('');
    setIsSending(true);
    setStreamingAnswer('');

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

    // 创建 AbortController 用于中断请求
    abortControllerRef.current = new AbortController();

    // 模拟流式输出（逐字显示）
    // 实际 API 返回完整回答后，用定时器逐字追加
    try {
      const res = await apiSendMessage(convId, { question: trimmed, thinking_enabled: thinkingEnabled, system_context: factContext ?? undefined }, abortControllerRef.current.signal);
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
            setStreamingAnswer(null);
            void queryClient.invalidateQueries({ queryKey: ['assistant-messages', convId] });
            void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
            setIsSending(false);
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
  }, [inputText, isSending, selectedConvId, thinkingEnabled, queryClient]);

  // 清理定时器
  useEffect(() => {
    return () => {
      if (streamingTimerRef.current) {
        clearInterval(streamingTimerRef.current);
      }
    };
  }, []);

  // 切换对话时清空本地消息（但发送中不清，避免首次提问时用户消息一闪而过）
  useEffect(() => {
    if (!isSending) {
      setLocalMessages([]);
      setStreamingAnswer(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedConvId]);

  // 数据库消息到达后清空本地消息（避免重复显示）
  useEffect(() => {
    if (messagesData && messagesData.length > 0 && !isSending && streamingAnswer === null) {
      setLocalMessages([]);
    }
  }, [messagesData, isSending, streamingAnswer]);

  return (
    <div style={{ display: 'flex', gap: 16, height: 'calc(100vh - 140px)' }}>
      <style>{`
        .ant-list-item:hover .conv-actions {
          opacity: 1 !important;
        }
      `}</style>
      {/* ---- 左侧：对话列表 ---- */}
      <Card
        size="small"
        style={{ width: 260, display: 'flex', flexDirection: 'column' }}
        bodyStyle={{ padding: 0, flex: 1, overflow: 'auto' }}
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
        <List
          dataSource={conversationList}
          renderItem={(conv: ConversationSummary) => (
            <List.Item
              style={{
                padding: '8px 12px',
                cursor: 'pointer',
                position: 'relative',
                background: selectedConvId === conv.id ? '#e6f4ff' : 'transparent',
                transition: 'background 0.2s',
              }}
              onClick={() => setSelectedConvId(conv.id)}
            >
              <List.Item.Meta
                title={
                  <span style={{ fontWeight: selectedConvId === conv.id ? 600 : 400, fontSize: 14 }}>
                    {conv.title || '新对话'}
                  </span>
                }
                description={
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 2 }}>
                    <Tag
                      color={conv.provider_mode === 'offline' ? 'default' : 'blue'}
                      style={{ fontSize: 10, margin: 0 }}
                    >
                      {conv.provider_mode === 'offline' ? '离线' : '在线'}
                    </Tag>
                    {conv.pinned && <Tag color='gold' style={{ fontSize: 9, margin: 0, padding: '0 4px' }}>置顶</Tag>}
                    {conv.archived && <Tag color='default' style={{ fontSize: 9, margin: 0, padding: '0 4px' }}>归档</Tag>}
                  </div>
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
                  background: selectedConvId === conv.id ? '#e6f4ff' : '#fff',
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
                      style={{ padding: '0 4px', fontSize: 12, color: '#ff4d4f' }}
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
      </Card>

      {/* ---- 右侧：对话区域 ---- */}
      <Card
        size="small"
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        bodyStyle={{
          padding: 16,
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
        title={
          <Space>
            <Title level={5} style={{ margin: 0 }}>
              AI 助手
            </Title>
            {selectedConvId && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {conversationList.find((c) => c.id === selectedConvId)?.title ?? ''}
              </Text>
            )}
          </Space>
        }
        extra={<ProviderStatus />}
      >
        {/* 消息列表区域 */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '8px 4px',
            background: '#fafafa',
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
              <MessageThread messages={displayMessages} />
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
              size="small"
              type={factContext ? 'primary' : 'default'}
              onClick={() => setFactModalOpen(true)}
              style={{ flexShrink: 0 }}
            >
              {factContext ? '📊 数据已加载' : '插入实验数据'}
            </Button>
            {factContext && (
              <Tooltip title={`已加载: ${factContextLabel}（点击清除）`}>
                <Button
                  size="small"
                  type="link"
                  danger
                  onClick={handleClearFactContext}
                  style={{ flexShrink: 0, padding: '0 4px' }}
                >
                  ✕
                </Button>
              </Tooltip>
            )}
            <TextArea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="输入问题，Enter 发送"
              autoSize={{ minRows: 1, maxRows: 4 }}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              disabled={isSending}
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

      {/* 插入实验数据 Modal */}
      <Modal
        title="插入实验数据"
        open={factModalOpen}
        onOk={handleInsertFact}
        onCancel={() => { setFactModalOpen(false); setSelectedFactIds([]); }}
        confirmLoading={insertingFact}
        okText={`插入 ${selectedFactIds.length > 0 ? `(${selectedFactIds.length})` : ''}`}
        cancelText="取消"
        width={600}
      >
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary">选择一个或多个样品，数据将作为系统上下文随问题一起发送给 AI</Text>
        </div>
        <Select
          mode="multiple"
          placeholder="选择样品（支持搜索）"
          style={{ width: '100%' }}
          showSearch
          optionFilterProp="label"
          value={selectedFactIds}
          onChange={setSelectedFactIds}
          maxTagCount="responsive"
          options={(factsData?.items ?? []).map((f: FactSummary) => ({
            value: f.fact_id,
            label: `${f.subject_id}${f.task_name ? ` (${f.task_name})` : ''}`,
          }))}
        />
      </Modal>
    </div>
  );
}

export default AssistantPage;
