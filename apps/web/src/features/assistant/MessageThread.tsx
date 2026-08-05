/**
 * MessageThread — 消息线程组件（编排层）。
 *
 * 从 856 行单文件组件精简为编排层：
 * 调用子组件组合消息列表，目标 < 250 行。
 * 所有渲染逻辑已提取到 message-thread/components/ 和 message-thread/utils/ 子目录。
 *
 * Displays conversation history, distinguishing user messages,
 * AI responses, and tool messages.
 * AI responses use safe Markdown rendering (react-markdown + remark-gfm).
 * AI 消息内容块化渲染：echarts/plotly/table/conclusion 块可加入橱窗。
 *
 * H-14: No dangerouslySetInnerHTML, no regex-based HTML construction.
 */

import { Typography } from 'antd';
import 'katex/dist/katex.min.css';
import type { AssistantMessage } from '@/api/models-ai';
import { MessageBubble } from './message-thread/components/MessageBubble';

const { Text } = Typography;

export function MessageThread({
  messages,
  conversationId,
  systemContext,
}: {
  messages: AssistantMessage[];
  /** 当前对话 ID（传入 BlockWrapper 用于加入橱窗） */
  conversationId?: string | null;
  /** 当前对话的 system_context（用于解析 data_source） */
  systemContext?: string | null;
}): JSX.Element {
  if (messages.length === 0) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          color: 'var(--ocean-text-muted)',
        }}
      >
        <Text type="secondary">开始一段新对话吧</Text>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {messages.map((msg: AssistantMessage) => (
        <MessageBubble
          key={msg.id}
          msg={msg}
          conversationId={conversationId}
          systemContext={systemContext}
        />
      ))}
    </div>
  );
}

export default MessageThread;
