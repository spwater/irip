/**
 * MessageThread 局部类型和常量。
 *
 * 从 MessageThread.tsx 提取，供主组件和子组件共用。
 */

/** Message role -> avatar letter */
export const ROLE_AVATAR_TEXT: Record<string, string> = {
  user: 'U',
  assistant: 'AI',
  tool: 'T',
};

/** Message role -> avatar color */
export const ROLE_COLOR: Record<string, string> = {
  user: '#1686AE',
  assistant: '#14765E',
  tool: '#9A6818',
};

/** Message role -> Chinese label */
export const ROLE_LABEL: Record<string, string> = {
  user: '我',
  assistant: '小艾',
  tool: '工具',
};

/** BlockifiedMarkdown 组件 Props */
export interface BlockifiedMarkdownProps {
  content: string;
  messageId: string;
  conversationId: string | null;
  systemContext: string | null | undefined;
}

/** ChartBlock 组件 Props */
export interface ChartBlockProps {
  optionStr: string;
}

/** MessageBubble 组件 Props */
export interface MessageBubbleProps {
  msg: import('@/api/models-ai').AssistantMessage;
  conversationId?: string | null;
  systemContext?: string | null;
}
