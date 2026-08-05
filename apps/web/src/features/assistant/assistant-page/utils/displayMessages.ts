/**
 * 消息合并纯函数 — 从 AssistantPage.tsx 提取。
 *
 * 合并本地消息和数据库消息，处理流式输出场景。
 */

import type { AssistantMessage } from '@/api/models-ai';

/**
 * 合并本地消息和数据库消息。
 *
 * 流式输出中：显示历史消息 + 本地用户消息 + 流式 AI 消息。
 * 非流式：合并数据库消息和本地消息（避免重复）。
 *
 * @param dbMessages 数据库消息列表
 * @param localMessages 本地消息列表（用户消息立即显示）
 * @param streamingAnswer 流式回答内容（非 null 表示正在流式输出）
 * @param selectedConvId 当前对话 ID
 */
export function mergeDisplayMessages(
  dbMessages: AssistantMessage[],
  localMessages: AssistantMessage[],
  streamingAnswer: string | null,
  selectedConvId: string | null,
): AssistantMessage[] {
  if (streamingAnswer !== null) {
    // 流式输出中：显示历史消息 + 本地用户消息 + 流式 AI 消息
    const localIds = new Set(localMessages.map((m) => m.id));
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
    return [...dbHistory, ...localMessages, aiMsg];
  }
  // 非流式：合并数据库消息和本地消息（避免重复）
  if (localMessages.length > 0 && dbMessages.length === 0) {
    return localMessages;
  }
  return dbMessages;
}

/**
 * 从 system_context 字符串中提取样品标签。
 *
 * @param systemContext system_context 字符串
 * @returns 标签数组（如 ['样品A', '样品B']），无匹配返回空数组
 */
export function extractFactLabels(systemContext: string): string[] {
  return (systemContext.match(/### 样品: (.+)/g) || [])
    .map((s) => s.replace('### 样品: ', ''));
}
