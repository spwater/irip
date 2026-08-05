/**
 * MessageBubble — 单条消息气泡组件。
 *
 * 从 MessageThread.tsx 提取。包含头像、消息内容（用户/AI 分支渲染）、
 * 工具调用追踪、引用列表、不确定性提示。
 */

import { Avatar, Tag, Typography } from 'antd';
import CitationList from '@/features/assistant/CitationList';
import ToolTrace from '@/features/assistant/ToolTrace';
import type { Citation, ToolCallSummary } from '@/api/models-ai';
import { renderMentions } from '../utils/mentionUtils';
import { BlockifiedMarkdown } from './BlockifiedMarkdown';
import { ROLE_AVATAR_TEXT, ROLE_COLOR, ROLE_LABEL, type MessageBubbleProps } from '../types';

const { Text, Paragraph } = Typography;

// KaTeX 样式隔离已在 global.css 中处理（仅 .katex { line-height: 1.2 }）
// 不在此处添加任何 KaTeX 覆盖样式，避免与 KaTeX 自身 CSS 冲突
const katexStyle = '';

export function MessageBubble({
  msg,
  conversationId,
  systemContext,
}: MessageBubbleProps): JSX.Element {
  const isUser = msg.role === 'user';
  const toolCalls: ToolCallSummary[] = msg.tool_calls ?? [];
  const citations: Citation[] = msg.citations ?? [];
  // irip-ai-collab: 发送者头像和显示名
  const senderAvatarUrl = msg.sender_avatar_url;
  const senderDisplayName = msg.sender_display_name;
  // irip-ai-collab: 用户消息右对齐浅蓝背景，AI 消息左对齐灰色背景
  const senderInitial = senderDisplayName ? senderDisplayName.charAt(0).toUpperCase() : 'U';

  return (
    <div
      id={`msg-${msg.id}`}
      style={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        gap: 12,
        alignItems: 'flex-start',
      }}
    >
      <Avatar
        size={36}
        src={isUser ? senderAvatarUrl : undefined}
        style={{
          backgroundColor: isUser
            ? (senderAvatarUrl ? 'transparent' : ROLE_COLOR['user'])
            : ROLE_COLOR[msg.role] ?? '#1686AE',
          flexShrink: 0,
          fontSize: 14,
          fontWeight: 600,
        }}
      >
        {isUser ? (senderAvatarUrl ? null : senderInitial) : (ROLE_AVATAR_TEXT[msg.role] ?? 'AI')}
      </Avatar>
      <div
        style={{
          maxWidth: 'calc(100% - 48px)',
          padding: '12px 16px',
          borderRadius: 12,
          background: isUser ? 'rgba(22, 134, 174, 0.10)' : 'rgba(20, 118, 94, 0.06)',
          border: `1px solid ${isUser ? 'rgba(22, 134, 174, 0.20)' : 'rgba(20, 118, 94, 0.18)'}`,
        }}
      >
        <div style={{ marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
          {isUser && senderDisplayName ? (
            <>
              <Text type="secondary" style={{ fontSize: 11, fontWeight: 600 }}>
                {senderDisplayName}
              </Text>
              {msg.mentions && msg.mentions.length > 0 && (
                <Tag
                  color="blue"
                  style={{ fontSize: 10, margin: 0, padding: '0 4px', lineHeight: '16px' }}
                >
                  @{msg.mentions.length}
                </Tag>
              )}
            </>
          ) : (
            <Text type="secondary" style={{ fontSize: 11, fontWeight: 600 }}>
              {ROLE_LABEL[msg.role] ?? msg.role}
            </Text>
          )}
        </div>
        {isUser ? (
          <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
            {renderMentions(msg.content, msg.mentions)}
          </Paragraph>
        ) : (
          <div className="ai-markdown-body">
            <style>{katexStyle}</style>
            <BlockifiedMarkdown
              content={msg.content}
              messageId={msg.id}
              conversationId={conversationId ?? null}
              systemContext={systemContext}
            />
          </div>
        )}

        {/* Tool call traces (AI messages only) */}
        {!isUser && toolCalls.length > 0 && <ToolTrace toolCalls={toolCalls} />}

        {/* Citation list (AI messages only) */}
        {!isUser && citations.length > 0 && <CitationList citations={citations} />}

        {/* Uncertainty note */}
        {!isUser && msg.uncertainty && (
          <div style={{ marginTop: 8 }}>
            <Text type="warning" style={{ fontSize: 12 }}>
              {'⚠'} {msg.uncertainty}
            </Text>
          </div>
        )}
      </div>
    </div>
  );
}

export default MessageBubble;
