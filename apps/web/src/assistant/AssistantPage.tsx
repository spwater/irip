import { useEffect, useRef, useState } from 'react';
import {
  Button,
  Card,
  Input,
  List,
  Modal,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import ProviderStatus from '@/assistant/ProviderStatus';
import MessageThread from '@/assistant/MessageThread';
import {
  apiCreateConversation,
  apiListConversations,
  apiListMessages,
  apiSendMessage,
  extractApiError,
  type AssistantMessage,
  type ConversationSummary,
} from '@/api/client';

const { Title, Text } = Typography;
const { TextArea } = Input;

/**
 * AI 助手主页面
 *
 * 功能：
 * - 对话列表侧栏（创建新对话、切换对话）
 * - 消息列表（用户/AI/工具消息）
 * - 输入框 + 发送按钮
 * - Provider 状态标签（离线模拟/OpenAI 兼容）
 * - 工具调用轨迹展示（MessageThread 内嵌）
 * - 引用列表（可点击跳转，MessageThread 内嵌）
 */
export function AssistantPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [inputText, setInputText] = useState('');
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [newConvTitle, setNewConvTitle] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // ---- 对话列表查询 ----
  const { data: conversations } = useQuery({
    queryKey: ['assistant-conversations'],
    queryFn: () => apiListConversations(),
    retry: false,
  });

  const conversationList: ConversationSummary[] = conversations ?? [];

  // ---- 消息列表查询（依赖选中对话） ----
  const { data: messagesData, isLoading: messagesLoading } = useQuery({
    queryKey: ['assistant-messages', selectedConvId],
    queryFn: () => apiListMessages(selectedConvId!),
    enabled: !!selectedConvId,
    retry: false,
  });

  const messages: AssistantMessage[] = messagesData ?? [];

  // ---- 发送消息 Mutation ----
  const sendMutation = useMutation({
    mutationFn: (vars: { conversationId: string; question: string }) =>
      apiSendMessage(vars.conversationId, { question: vars.question }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['assistant-messages', selectedConvId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['assistant-conversations'],
      });
      setInputText('');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 创建对话 Mutation ----
  const createConvMutation = useMutation({
    mutationFn: (title: string) =>
      apiCreateConversation({ title, provider_mode: 'offline' }),
    onSuccess: (conv: ConversationSummary) => {
      void queryClient.invalidateQueries({
        queryKey: ['assistant-conversations'],
      });
      setSelectedConvId(conv.id);
      setCreateModalOpen(false);
      setNewConvTitle('');
      message.success('对话已创建');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 自动滚动到底部 ----
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // ---- 事件处理 ----
  const handleSend = (): void => {
    const trimmed = inputText.trim();
    if (!trimmed || !selectedConvId) {
      if (!selectedConvId) {
        message.warning('请先选择或创建一个对话');
      }
      return;
    }
    sendMutation.mutate({ conversationId: selectedConvId, question: trimmed });
  };

  const handleCreateConversation = (): void => {
    createConvMutation.mutate(newConvTitle || '');
  };

  return (
    <div style={{ display: 'flex', gap: 16, height: 'calc(100vh - 140px)' }}>
      {/* ---- 左侧：对话列表 ---- */}
      <Card
        size="small"
        style={{ width: 260, display: 'flex', flexDirection: 'column' }}
        bodyStyle={{ padding: 0, flex: 1, overflow: 'auto' }}
        title={
          <Space>
            <Title level={5} style={{ margin: 0 }}>
              对话列表
            </Title>
          </Space>
        }
        extra={
          <Button
            type="primary"
            size="small"
            onClick={() => setCreateModalOpen(true)}
          >
            新建
          </Button>
        }
      >
        <List
          dataSource={conversationList}
          renderItem={(conv: ConversationSummary) => (
            <List.Item
              style={{
                padding: '8px 12px',
                cursor: 'pointer',
                background:
                  selectedConvId === conv.id ? '#e6f4ff' : 'transparent',
                transition: 'background 0.2s',
              }}
              onClick={() => setSelectedConvId(conv.id)}
            >
              <div style={{ width: '100%' }}>
                <Text
                  ellipsis
                  style={{ fontWeight: selectedConvId === conv.id ? 600 : 400 }}
                >
                  {conv.title || '未命名对话'}
                </Text>
                <div>
                  <Tag
                    color={conv.provider_mode === 'offline' ? 'default' : 'blue'}
                    style={{ fontSize: 10 }}
                  >
                    {conv.provider_mode === 'offline' ? '离线' : '在线'}
                  </Tag>
                </div>
              </div>
            </List.Item>
          )}
          locale={{ emptyText: '暂无对话' }}
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
                {conversationList.find((c) => c.id === selectedConvId)?.title ??
                  ''}
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
          {!selectedConvId ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
              }}
            >
              <Text type="secondary">
                请从左侧选择或创建一个对话开始提问
              </Text>
            </div>
          ) : messagesLoading ? (
            <Text type="secondary">加载消息中...</Text>
          ) : (
            <>
              <MessageThread messages={messages} />
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* 输入区域 */}
        <div>
          <Space.Compact style={{ width: '100%' }}>
            <TextArea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder={
                selectedConvId
                  ? '输入问题，如「D50 参数的溯源链路是什么？」'
                  : '请先选择或创建对话'
              }
              autoSize={{ minRows: 1, maxRows: 4 }}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              disabled={sendMutation.isPending}
            />
          </Space.Compact>
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
            <Button
              type="primary"
              onClick={handleSend}
              loading={sendMutation.isPending}
              disabled={!selectedConvId || !inputText.trim()}
            >
              发送
            </Button>
          </div>
        </div>
      </Card>

      {/* 新建对话 Modal */}
      <Modal
        title="新建对话"
        open={createModalOpen}
        onOk={handleCreateConversation}
        onCancel={() => {
          setCreateModalOpen(false);
          setNewConvTitle('');
        }}
        confirmLoading={createConvMutation.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Input
          placeholder="对话标题（可选，留空自动生成）"
          value={newConvTitle}
          onChange={(e) => setNewConvTitle(e.target.value)}
          maxLength={200}
          onPressEnter={handleCreateConversation}
        />
      </Modal>
    </div>
  );
}

export default AssistantPage;
