import { Avatar, Typography } from 'antd';
import CitationList from '@/assistant/CitationList';
import ToolTrace from '@/assistant/ToolTrace';
import type { AssistantMessage, Citation, ToolCallSummary } from '@/api/client';

const { Text, Paragraph } = Typography;

/**
 * 消息角色 → 头像首字母
 */
const ROLE_AVATAR_TEXT: Record<string, string> = {
  user: 'U',
  assistant: 'AI',
  tool: 'T',
};

/**
 * 消息角色 → 头像颜色
 */
const ROLE_COLOR: Record<string, string> = {
  user: '#1677ff',
  assistant: '#52c41a',
  tool: '#fa8c16',
};

/**
 * 消息角色 → 中文名
 */
const ROLE_LABEL: Record<string, string> = {
  user: '我',
  assistant: 'AI 助手',
  tool: '工具',
};

/**
 * 消息列表组件
 *
 * 展示对话历史，区分用户消息、AI 回答与工具消息。
 * AI 回答附带工具调用轨迹与引用列表。
 */
export function MessageThread({
  messages,
}: {
  messages: AssistantMessage[];
}): JSX.Element {
  if (messages.length === 0) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          color: '#999',
        }}
      >
        <Text type="secondary">开始一段新对话吧 ✨</Text>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {messages.map((msg: AssistantMessage) => {
        const isUser = msg.role === 'user';
        const toolCalls: ToolCallSummary[] = msg.tool_calls ?? [];
        const citations: Citation[] = msg.citations ?? [];

        return (
          <div
            key={msg.id}
            style={{
              display: 'flex',
              flexDirection: isUser ? 'row-reverse' : 'row',
              gap: 12,
              alignItems: 'flex-start',
            }}
          >
            <Avatar
              size={36}
              style={{
                backgroundColor: ROLE_COLOR[msg.role] ?? '#1677ff',
                flexShrink: 0,
                fontSize: 14,
                fontWeight: 600,
              }}
            >
              {ROLE_AVATAR_TEXT[msg.role] ?? 'AI'}
            </Avatar>
            <div
              style={{
                maxWidth: '75%',
                padding: '12px 16px',
                borderRadius: 12,
                background: isUser ? '#e6f4ff' : '#f6ffed',
                border: `1px solid ${isUser ? '#91caff' : '#b7eb8f'}`,
              }}
            >
              <div style={{ marginBottom: 4 }}>
                <Text
                  type="secondary"
                  style={{ fontSize: 11, fontWeight: 600 }}
                >
                  {ROLE_LABEL[msg.role] ?? msg.role}
                </Text>
              </div>
              <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                {msg.content}
              </Paragraph>

              {/* 工具调用轨迹（仅 AI 消息） */}
              {!isUser && toolCalls.length > 0 && <ToolTrace toolCalls={toolCalls} />}

              {/* 引用列表（仅 AI 消息） */}
              {!isUser && citations.length > 0 && (
                <CitationList citations={citations} />
              )}

              {/* 不确定性说明 */}
              {!isUser && msg.uncertainty && (
                <div style={{ marginTop: 8 }}>
                  <Text
                    type="warning"
                    style={{ fontSize: 12 }}
                  >
                    ⚠ {msg.uncertainty}
                  </Text>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default MessageThread;
