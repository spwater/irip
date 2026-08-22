/**
 * MessageInputArea — 底部输入区域（思考开关 + 载入数据 + @人输入 + 发送/中断按钮）。
 *
 * 从 AssistantPage.tsx 提取。
 */

import {
  Button,
  Space,
  Switch,
  Tooltip,
  Typography,
} from 'antd';
import MentionInput from '@/features/assistant/MentionInput';
import type { MessageInputAreaProps } from '../types';

const { Text } = Typography;

export function MessageInputArea(props: MessageInputAreaProps): JSX.Element {
  const {
    inputText,
    setInputText,
    mentions,
    setMentions,
    isSending,
    thinkingEnabled,
    setThinkingEnabled,
    factContext,
    factContextLabel,
    onOpenFactModal,
    onClearFactContext,
    onSend,
    onCancelRequest,
    isCollaborative,
    participantsData,
  } = props;

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <Tooltip title="开启后 AI 会先思考再回答。">
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, paddingBottom: 8, flexShrink: 0 }}>
            <Switch
              size="small"
              checked={thinkingEnabled}
              onChange={setThinkingEnabled}
            />
            <Text type="secondary" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
              思考
            </Text>
          </div>
        </Tooltip>
        <Button
          type={factContext ? 'primary' : 'default'}
          onClick={onOpenFactModal}
          style={{ flexShrink: 0, height: 'auto', minHeight: 32, alignSelf: 'stretch' }}
        >
          {factContext ? '📊 数据已加载' : '载入实验数据'}
        </Button>
        {factContext && (
          <Tooltip title={`已加载: ${factContextLabel}（点击清除）`}>
            <Button
              type="link"
              danger
              onClick={onClearFactContext}
              style={{ flexShrink: 0, padding: '0 4px', height: 'auto', minHeight: 32, alignSelf: 'stretch' }}
            >
              ✕
            </Button>
          </Tooltip>
        )}
        <MentionInput
          value={inputText}
          onChange={setInputText}
          mentions={mentions}
          onMentionsChange={setMentions}
          placeholder="输入问题，@ 提及成员，Enter 发送"
          disabled={isSending}
          isCollaborative={isCollaborative}
          participants={participantsData ?? []}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              void onSend();
            }
          }}
          style={{ flex: 1 }}
        />
      </div>
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
        <Space>
          <Button
            type="primary"
            onClick={() => void onSend()}
            loading={isSending}
            disabled={!inputText.trim()}
          >
            发送
          </Button>
          {isSending && (
            <Button
              danger
              onClick={onCancelRequest}
              style={{ fontWeight: 700 }}
              title="中断对话"
            >
              ■
            </Button>
          )}
        </Space>
      </div>
    </div>
  );
}

export default MessageInputArea;
