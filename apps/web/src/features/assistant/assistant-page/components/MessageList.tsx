/**
 * MessageList — 右侧对话区域（参与者工具栏 + 消息列表）。
 *
 * 从 AssistantPage.tsx 提取。包含 AI 状态指示、参与者头像组、
 * 消息列表区域（含空状态、流式加载提示）。
 */

import {
  Avatar,
  Button,
  Card,
  Space,
  Typography,
} from 'antd';
import { TeamOutlined, UserAddOutlined } from '@ant-design/icons';
import MessageThread from '@/features/assistant/MessageThread';
import type { AssistantMessage } from '@/api/models-ai';
import type { Participant } from '@/api/collaboration';

const { Text } = Typography;

export interface MessageListPropsExtended {
  /** 合并后的消息列表 */
  displayMessages: AssistantMessage[];
  /** 是否正在发送/流式输出 */
  isSending: boolean;
  /** 流式回答内容 */
  streamingAnswer: string | null;
  /** 当前对话 ID */
  selectedConvId: string | null;
  /** 实验数据上下文 */
  factContext: string | null;
  /** 滚动锚点 ref */
  messagesEndRef: React.RefObject<HTMLDivElement>;
  /** AI 在线状态 */
  aiOnline: boolean;
  /** 参与者列表 */
  participantsData: Participant[] | undefined;
  /** 是否为 owner */
  isOwner: boolean;
  /** 打开邀请 Modal */
  onOpenInviteModal: () => void;
  /** 打开参与者 Drawer */
  onOpenParticipantDrawer: () => void;
  /** 输入区域子组件（渲染在 Card 内部底部） */
  children?: React.ReactNode;
}

export function MessageList(props: MessageListPropsExtended): JSX.Element {
  const {
    displayMessages,
    isSending,
    streamingAnswer,
    selectedConvId,
    factContext,
    messagesEndRef,
    aiOnline,
    participantsData,
    isOwner,
    onOpenInviteModal,
    onOpenParticipantDrawer,
    children,
  } = props;

  return (
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
                <Button size="small" type="text" icon={<TeamOutlined />} onClick={onOpenParticipantDrawer}>
                  {(participantsData ?? []).length}人
                </Button>
              </>
            )}
          </Space>
          {isOwner && (
            <Button size="small" type="primary" ghost icon={<UserAddOutlined />} onClick={onOpenInviteModal}>
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
      {/* 输入区域（children 渲染在 Card 内部底部） */}
      {children}
    </Card>
  );
}

export default MessageList;
