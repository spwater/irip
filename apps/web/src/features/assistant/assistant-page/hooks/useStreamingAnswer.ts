/**
 * useStreamingAnswer — 流式回答状态管理 hook。
 *
 * 从 AssistantPage.tsx 提取。管理本地消息缓存、流式回答内容、
 * 发送状态、中断控制等核心状态。
 *
 * 注意：今天刚把 AI 回答从模拟逐字改为真 SSE 流式（apiSendMessageStream + for await），
 * 此 hook 严格保持原有 SSE 逻辑不变。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import {
  apiCancelRequest,
  apiCreateConversation,
  apiSendMessage,
  apiSendMessageStream,
  type AssistantMessage,
  type ConversationSummary,
} from '@/api/models-ai';
import { extractApiError } from '@/api/types';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { extractFactLabels } from '../utils/displayMessages';
import type { UseStreamingAnswerResult } from '../types';

export interface UseStreamingAnswerParams {
  selectedConvId: string | null;
  setSelectedConvId: (id: string | null) => void;
  thinkingEnabled: boolean;
  factContext: string | null;
  setFactContext: (v: string | null) => void;
  setFactContextLabel: (v: string | null) => void;
  isCollaborative: boolean;
  conversationList: ConversationSummary[];
  /** DB 消息数据（用于清空本地消息避免重复） */
  messagesData: AssistantMessage[] | undefined;
  /** isSending 状态（由主组件管理，传递给此 hook） */
  isSending: boolean;
  /** isSending setter（由主组件管理，传递给此 hook） */
  setIsSending: (v: boolean) => void;
}

export function useStreamingAnswer(params: UseStreamingAnswerParams): UseStreamingAnswerResult {
  const {
    selectedConvId,
    setSelectedConvId,
    thinkingEnabled,
    factContext,
    setFactContext,
    setFactContextLabel,
    isCollaborative,
    conversationList,
    messagesData,
    isSending,
    setIsSending,
  } = params;

  const queryClient = useQueryClient();
  const currentUser = useAuthStore((s) => s.user);

  const [inputText, setInputText] = useState('');
  const [mentions, setMentions] = useState<string[]>([]);

  // 本地消息缓存：用户消息立即显示 + AI 回答流式追加
  const [localMessages, setLocalMessages] = useState<AssistantMessage[]>([]);
  // 流式回答的临时内容
  const [streamingAnswer, setStreamingAnswer] = useState<string | null>(null);
  // AI 正在思考中（收到 reasoning 事件但尚未收到 content 事件）
  const [isThinking, setIsThinking] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const streamingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** 从对话的 system_context 恢复实验数据上下文 */
  const restoreContextFromConversation = useCallback((conv: ConversationSummary | undefined) => {
    if (conv?.system_context) {
      setFactContext(conv.system_context);
      // 从 system_context 里提取样品标签
      const labels = extractFactLabels(conv.system_context);
      setFactContextLabel(labels.length > 0 ? labels.join(', ') : '已加载');
    } else {
      setFactContext(null);
      setFactContextLabel(null);
    }
  }, [setFactContext, setFactContextLabel]);

  // ---- 切换对话时清空本地消息，恢复该对话关联的实验数据上下文 ----
  // 注意：依赖项含 conversationList，确保对话列表加载完成后也能恢复 system_context
  useEffect(() => {
    if (!isSending) {
      setLocalMessages([]);
      setStreamingAnswer(null);
      // 从对话列表里找到选中的对话，恢复其 system_context
      const conv = conversationList.find((c) => c.id === selectedConvId);
      restoreContextFromConversation(conv);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedConvId, conversationList]);

  // 数据库消息到达后清空本地消息（避免重复显示）
  useEffect(() => {
    if (messagesData && messagesData.length > 0 && !isSending && streamingAnswer === null) {
      setLocalMessages([]);
    }
  }, [messagesData, isSending, streamingAnswer]);

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
    setIsThinking(false);

    // 创建 AbortController 用于中断请求
    abortControllerRef.current = new AbortController();

    // 真实 SSE 流式输出
    try {
      const stream = apiSendMessageStream(
        convId,
        {
          question: trimmed,
          thinking_enabled: thinkingEnabled,
          system_context: factContext ?? undefined,
          mentions: currentMentions.length > 0 ? currentMentions : undefined,
        },
        abortControllerRef.current.signal,
      );

      for await (const event of stream) {
        if (event.type === 'reasoning') {
          // 收到思考过程增量，标记为思考中
          setIsThinking(true);
        } else if (event.type === 'chunk') {
          // 收到实际回答内容，取消思考中状态
          setIsThinking(false);
          setStreamingAnswer((prev) => (prev ?? '') + event.content);
        } else if (event.type === 'done') {
          // 设置最终 answer（含工具调用后的完整回答）
          // 追加而非覆盖：第一轮流式输出的内容（如画图代码块）保留，
          // 第二轮 completion 的回答追加在后面
          setIsThinking(false);
          const doneAnswer = event.answer || '(无回答)';
          setStreamingAnswer((prev) => (prev ? prev + '\n\n' + doneAnswer : doneAnswer));
          // invalidate 拉取 DB 消息
          void queryClient.invalidateQueries({ queryKey: ['assistant-messages', convId] });
          void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
          // 延迟 100ms 让 DB 消息先到达，再清 streamingAnswer，避免中间空白闪烁
          streamingTimeoutRef.current = setTimeout(() => {
            streamingTimeoutRef.current = null;
            setStreamingAnswer(null);
            setIsSending(false);
          }, 100);
        } else if (event.type === 'error') {
          setIsThinking(false);
          setStreamingAnswer(null);
          message.error(event.message);
          setIsSending(false);
          abortControllerRef.current = null;
          void queryClient.invalidateQueries({ queryKey: ['assistant-messages', convId] });
        }
      }
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
      setIsThinking(false);
      setIsSending(false);
      abortControllerRef.current = null;
      // 刷新数据库消息（可能用户消息已保存但 AI 回答失败）
      void queryClient.invalidateQueries({ queryKey: ['assistant-messages', convId] });
    }
  }, [inputText, isSending, selectedConvId, thinkingEnabled, factContext, queryClient, mentions, currentUser, isCollaborative, setSelectedConvId, setIsSending]);

  // ---- 中断请求 ----
  const handleCancelRequest = useCallback((): void => {
    // 1. 通知后端取消 AI 请求
    if (selectedConvId) {
      void apiCancelRequest(selectedConvId);
    }
    // 2. 中断前端 fetch 流式请求
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    // 3. 重置状态
    setStreamingAnswer(null);
    setIsThinking(false);
    setIsSending(false);
    void queryClient.invalidateQueries({ queryKey: ['assistant-messages', selectedConvId] });
  }, [selectedConvId, queryClient, setIsSending]);

  // 组件卸载时清理 setTimeout，避免对已卸载组件的状态更新
  useEffect(() => {
    return () => {
      if (streamingTimeoutRef.current) {
        clearTimeout(streamingTimeoutRef.current);
        streamingTimeoutRef.current = null;
      }
    };
  }, []);

  return {
    localMessages,
    streamingAnswer,
    isThinking,
    isSending,
    inputText,
    setInputText,
    setLocalMessages,
    setStreamingAnswer,
    setIsSending,
    mentions,
    setMentions,
    abortControllerRef,
    messagesEndRef,
    restoreContextFromConversation,
    handleSend,
    handleCancelRequest,
  };
}
