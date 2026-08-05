/**
 * @人 文本高亮渲染纯函数。
 *
 * 从 MessageThread.tsx 提取。
 */

import type { ReactNode } from 'react';

/**
 * irip-ai-collab: 高亮渲染 @人 文本。
 *
 * 将消息内容中 @ 开头的提及文字高亮为蓝色背景。
 * 简单方案：检测文本中的 @xxx 模式并高亮。
 */
export function renderMentions(content: string, mentions: string[] | undefined): ReactNode {
  if (!mentions || mentions.length === 0) {
    return content;
  }
  // 匹配 @后面紧跟非空白字符的模式
  const parts: ReactNode[] = [];
  const regex = /@(\S+)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = regex.exec(content)) !== null) {
    // 添加 @ 之前的普通文本
    if (match.index > lastIndex) {
      parts.push(content.slice(lastIndex, match.index));
    }
    // 高亮 @人名
    parts.push(
      <span
        key={`mention-${key}`}
        style={{
          backgroundColor: 'rgba(22, 134, 174, 0.15)',
          color: '#1686AE',
          borderRadius: 3,
          padding: '0 2px',
          fontWeight: 500,
        }}
      >
        @{match[1]}
      </span>,
    );
    lastIndex = match.index + match[0].length;
    key++;
  }
  // 添加剩余文本
  if (lastIndex < content.length) {
    parts.push(content.slice(lastIndex));
  }
  return parts.length === 0 ? content : <>{parts}</>;
}
