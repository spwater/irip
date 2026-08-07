/**
 * AiAssistantPanel — AI 科研助手面板（阶段 2 从占位激活）
 *
 * 功能：
 * - 持续对话区：消息列表 + 输入框 + 发送按钮
 * - PlanReviewCard 集成（AI 生成计划后在对话区上方显示）
 * - 覆盖声明固定条（分析进行中显示在对话区底部）
 * - 主动建议（可折叠提示气泡）
 * - 分块进度显示（"批次 3/8 进行中"）
 * - 加载历史消息（apiListMessages，最近 50 条）
 * - 消息可包含代码块（只读展示 AI 生成的 Python）
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Input,
  Button,
  Space,
  Typography,
  Spin,
  Empty,
  Tooltip,
} from 'antd';
import { SendOutlined, UserOutlined, RobotOutlined, CodeOutlined } from '@ant-design/icons';
import {
  apiSendMessage,
  apiListMessages,
  type ConversationMessage,
  type PlanDetail,
  type CoverageDeclaration,
} from '../../api/research';
import { PlanReviewCard } from './PlanReviewCard';
import { KnowledgeSearchStatus } from './KnowledgeSearchStatus';

const { TextArea } = Input;

export type AiAssistantPanelProps = {
  workspaceId: string;
  runId?: string | null;
  plan?: PlanDetail | null;
  coverageDeclaration?: CoverageDeclaration | null;
  batchProgress?: { current: number; total: number } | null;
  onConfirmPlan?: () => void;
  onAdjustPlan?: () => void;
};

export function AiAssistantPanel({
  workspaceId,
  runId,
  plan,
  coverageDeclaration,
  batchProgress,
  onConfirmPlan,
  onAdjustPlan,
}: AiAssistantPanelProps) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // 加载历史消息
  const loadMessages = useCallback(async () => {
    try {
      const res = await apiListMessages(workspaceId, runId ?? undefined, 50);
      setMessages(res.items);
    } catch (err) {
      console.error('加载对话消息失败', err);
    } finally {
      setLoading(false);
    }
  }, [workspaceId, runId]);

  useEffect(() => {
    loadMessages();
  }, [loadMessages]);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 发送消息
  const handleSend = useCallback(async () => {
    if (!inputValue.trim() || sending) return;
    const message = inputValue.trim();
    setInputValue('');
    setSending(true);

    // 立即显示用户消息
    const tempUserMsg: ConversationMessage = {
      message_id: 'temp-' + Date.now(),
      workspace_id: workspaceId,
      role: 'user',
      content: { text: message },
      run_id: runId ?? null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);

    try {
      const aiReply = await apiSendMessage(workspaceId, message, runId ?? undefined);
      setMessages((prev) => [...prev, aiReply]);
    } catch {
      // 失败时显示错误消息
      const errorMsg: ConversationMessage = {
        message_id: 'error-' + Date.now(),
        workspace_id: workspaceId,
        role: 'assistant',
        content: { text: '抱歉，AI 助手暂时无法响应。请稍后重试。' },
        run_id: runId ?? null,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setSending(false);
    }
  }, [inputValue, sending, workspaceId, runId]);

  // 覆盖声明显示
  const coverageText = coverageDeclaration
    ? `自动模式: ${coverageDeclaration.analysis_mode} | 数据覆盖率 ${Math.round(coverageDeclaration.data_coverage_rate * 100)}% | LLM 阅读率 ${Math.round(coverageDeclaration.llm_read_rate * 100)}% | 是否抽样: ${coverageDeclaration.is_sampled ? '是' : '否'}`
    : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* PlanReviewCard（AI 生成计划后显示） */}
      {plan && onConfirmPlan && onAdjustPlan && (
        <PlanReviewCard
          plan={plan}
          workspaceId={workspaceId}
          onConfirm={onConfirmPlan}
          onAdjust={onAdjustPlan}
        />
      )}

      {/* 对话消息区 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <Spin tip="加载对话..." />
          </div>
        ) : messages.length === 0 ? (
          <Empty description="开始与 AI 助手对话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          messages.map((msg) => (
            <div
              key={msg.message_id}
              style={{
                display: 'flex',
                flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                marginBottom: 12,
                padding: '0 8px',
              }}
            >
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: msg.role === 'user' ? '#1890ff' : '#f0f0f0',
                  color: msg.role === 'user' ? '#fff' : '#595959',
                  marginRight: msg.role === 'user' ? 0 : 8,
                  marginLeft: msg.role === 'user' ? 8 : 0,
                  flexShrink: 0,
                }}
              >
                {msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
              </div>
              <div
                style={{
                  maxWidth: '75%',
                  background: msg.role === 'user' ? '#e6f7ff' : '#f5f5f5',
                  borderRadius: 8,
                  padding: '8px 12px',
                }}
              >
                <Typography.Text style={{ fontSize: 14, whiteSpace: 'pre-wrap' }}>
                  {msg.content.text}
                </Typography.Text>
                {/* 代码块 */}
                {msg.content.code_blocks && msg.content.code_blocks.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    {msg.content.code_blocks.map((code, idx) => (
                      <Tooltip key={idx} title="AI 生成的代码（只读）">
                        <pre
                          style={{
                            background: '#282c34',
                            color: '#abb2bf',
                            padding: '8px 12px',
                            borderRadius: 4,
                            overflowX: 'auto',
                            fontSize: 12,
                            margin: '4px 0',
                          }}
                        >
                          <CodeOutlined style={{ marginRight: 4, opacity: 0.5 }} />
                          {code}
                        </pre>
                      </Tooltip>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 覆盖声明固定条 */}
      {coverageText && (
        <div
          style={{
            padding: '6px 10px',
            background: '#f5f5f5',
            borderRadius: 4,
            marginBottom: 8,
            fontSize: 12,
            color: '#595959',
          }}
        >
          {coverageText}
          {batchProgress && (
            <span style={{ marginLeft: 12, color: '#1890ff' }}>
              批次 {batchProgress.current}/{batchProgress.total} 进行中
            </span>
          )}
          {/* 知识库检索状态（阶段 5 新增） */}
          <div style={{ marginTop: 4 }}>
            <KnowledgeSearchStatus
              status={coverageDeclaration?.knowledge_search_status ?? 'not_applicable'}
              referenceCount={coverageDeclaration?.knowledge_reference_count ?? 0}
            />
          </div>
        </div>
      )}

      {/* 输入区 */}
      <Space.Compact style={{ width: '100%' }}>
        <TextArea
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="向 AI 助手提问..."
          autoSize={{ minRows: 1, maxRows: 4 }}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          style={{ borderRadius: '6px 0 0 6px' }}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={handleSend}
          loading={sending}
          style={{ borderRadius: '0 6px 6px 0' }}
        >
          发送
        </Button>
      </Space.Compact>
    </div>
  );
}
