import { useMemo, useRef, useState } from 'react';
import { Avatar, Dropdown, Input, Tag, Typography } from 'antd';
import { type MentionableUser } from '@/api/collaboration';

const { Text } = Typography;
const { TextArea } = Input;

/** AI 助手在 mentions 数组中的标识符 */
const AI_MENTION_ID = 'ai';

/** 协作对话中置顶的 AI 助手选项 */
const AI_ASSISTANT_USER: MentionableUser = {
  id: AI_MENTION_ID,
  display_name: '小艾',
  avatar_url: null,
  roles: ['AI助手'],
};

/**
 * @人输入组件（irip-ai-collab）。
 *
 * 基于 Ant Design TextArea 扩展，输入 @ 时弹出成员列表，
 * 选中后插入 @显示名 到文本并记录 user_id。
 *
 * 受控接口：
 * - value / onChange: 文本内容
 * - mentions / onMentionsChange: @ 的 user_id 数组
 * - isCollaborative: 是否为协作对话（参与者 > 1）
 *   - true: @列表首位展示「小艾（AI助手）」，选中后 mentions 中加入 "ai" 标识
 *   - false: 不显示 AI 选项，AI 自动回复
 *
 * 内部维护 mention 元数据 { userId, displayName, startIdx, endIdx }，
 * onChange 时检测文本中 @displayName 是否完整存在，不存在则移除对应 mention。
 */
export function MentionInput({
  value,
  onChange,
  mentions,
  onMentionsChange,
  placeholder,
  disabled,
  onPressEnter,
  style,
  isCollaborative,
  participants,
}: {
  value: string;
  onChange: (text: string) => void;
  mentions: string[];
  onMentionsChange: (mentions: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  onPressEnter?: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  style?: React.CSSProperties;
  isCollaborative?: boolean;
  /** 对话参与者列表（仅显示参与者 + 小艾，而非全 org 用户） */
  participants?: Array<{ user_id: string; display_name: string; avatar_url: string | null }>;
}): JSX.Element {
  // irip-ai-collab: @人列表 = 对话参与者 + 小艾（不再查全 org 用户）
  const mentionableUsers = useMemo(() => {
    return (participants ?? []).map((p) => ({
      id: p.user_id,
      display_name: p.display_name,
      avatar_url: p.avatar_url,
      roles: [],
    }));
  }, [participants]);

  // mention 元数据：{ userId, displayName, start, end }
  const mentionMetaRef = useRef<Array<{ userId: string; displayName: string; start: number; end: number }>>([]);
  // @ 触发状态
  const [mentionSearch, setMentionSearch] = useState<string | null>(null);
  // 光标位置
  const cursorRef = useRef(0);
  // TextArea ref
  const textAreaRef = useRef<HTMLTextAreaElement>(null);

  // 合并后的 @ 用户列表：协作对话中首位为「小艾（AI助手）」
  const allMentionableUsers = useMemo(() => {
    const realUsers = mentionableUsers ?? [];
    if (isCollaborative) {
      return [AI_ASSISTANT_USER, ...realUsers];
    }
    return realUsers;
  }, [mentionableUsers, isCollaborative]);

  // 处理文本变化
  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newText = e.target.value;
    cursorRef.current = e.target.selectionStart ?? newText.length;

    // 检测 @ 触发：光标前最近一个 @ 后的文字
    const beforeCursor = newText.substring(0, cursorRef.current);
    const atMatch = beforeCursor.match(/@([^\s@]*)$/);
    if (atMatch) {
      setMentionSearch(atMatch[1]);
    } else {
      setMentionSearch(null);
    }

    // 检测被删除的 mention（文本中不再包含 @displayName）
    const validMentions: string[] = [];
    const newMeta: Array<{ userId: string; displayName: string; start: number; end: number }> = [];
    for (const meta of mentionMetaRef.current) {
      const mentionText = `@${meta.displayName}`;
      // 在文本中查找 @displayName
      const idx = newText.indexOf(mentionText);
      if (idx !== -1) {
        validMentions.push(meta.userId);
        newMeta.push({ ...meta, start: idx, end: idx + mentionText.length });
      }
    }
    mentionMetaRef.current = newMeta;

    // 如果 mentions 数组有变化，通知父组件
    const sortedValid = Array.from(new Set(validMentions)).sort();
    const sortedCurrent = Array.from(new Set(mentions)).sort();
    if (sortedValid.join(',') !== sortedCurrent.join(',')) {
      onMentionsChange(sortedValid);
    }

    onChange(newText);
  };

  // 选择 @ 用户
  const handleSelectUser = (user: MentionableUser): void => {
    const text = value;
    const cursor = cursorRef.current;
    // 找到光标前的 @ 和搜索文本
    const beforeCursor = text.substring(0, cursor);
    const afterCursor = text.substring(cursor);
    const atMatch = beforeCursor.match(/@([^\s@]*)$/);
    if (!atMatch) return;

    const atStart = cursor - atMatch[0].length;
    const mentionText = `@${user.display_name}`;
    const newText = text.substring(0, atStart) + mentionText + ' ' + afterCursor;

    // 记录 mention 元数据
    mentionMetaRef.current.push({
      userId: user.id,
      displayName: user.display_name,
      start: atStart,
      end: atStart + mentionText.length,
    });

    // 更新 mentions 数组
    if (!mentions.includes(user.id)) {
      onMentionsChange([...mentions, user.id]);
    }

    onChange(newText);
    setMentionSearch(null);

    // 恢复光标位置到插入内容后
    requestAnimationFrame(() => {
      const newCursor = atStart + mentionText.length + 1;
      textAreaRef.current?.setSelectionRange(newCursor, newCursor);
      textAreaRef.current?.focus();
    });
  };

  // 过滤用户列表（含协作对话中的 AI 助手）
  const filteredUsers = useMemo(() => {
    if (mentionSearch === null) return [];
    const search = mentionSearch.toLowerCase();
    return allMentionableUsers.filter((u) =>
      u.display_name.toLowerCase().includes(search),
    );
  }, [mentionSearch, allMentionableUsers]);

  // 下拉菜单
  const menu = (
    <div
      style={{
        background: 'var(--ocean-surface-strong, #fff)',
        border: '1px solid var(--ocean-border-subtle, #f0f0f0)',
        borderRadius: 6,
        boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
        maxHeight: 280,
        overflow: 'auto',
        minWidth: 220,
        padding: 4,
      }}
    >
      {filteredUsers.length === 0 ? (
        <div style={{ padding: '8px 12px', textAlign: 'center' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {allMentionableUsers.length === 0 ? '暂无可 @ 的用户' : '未找到匹配用户'}
          </Text>
        </div>
      ) : (
        filteredUsers.map((user) => (
          <div
            key={user.id}
            role="button"
            tabIndex={0}
            onClick={() => handleSelectUser(user)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handleSelectUser(user);
              }
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '6px 10px',
              cursor: 'pointer',
              borderRadius: 4,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'rgba(22, 134, 174, 0.08)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
            }}
          >
            <Avatar size={24} src={user.avatar_url} style={{ flexShrink: 0 }}>
              {user.display_name.charAt(0)}
            </Avatar>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Text style={{ fontSize: 13, fontWeight: 500 }}>{user.display_name}</Text>
            </div>
            {user.roles.length > 0 && (
              <Tag style={{ fontSize: 10, margin: 0, padding: '0 4px' }}>
                {user.roles[0]}
              </Tag>
            )}
          </div>
        ))
      )}
    </div>
  );

  return (
    <Dropdown
      dropdownRender={() => menu}
      open={mentionSearch !== null && filteredUsers.length > 0}
      onOpenChange={(open) => {
        if (!open) setMentionSearch(null);
      }}
      trigger={[]}
      placement="topLeft"
    >
      <TextArea
        ref={textAreaRef as never}
        value={value}
        onChange={handleChange}
        placeholder={placeholder ?? '输入问题，@ 提及成员，Enter 发送'}
        autoSize={{ minRows: 1, maxRows: 4 }}
        onPressEnter={onPressEnter}
        disabled={disabled}
        style={style}
      />
    </Dropdown>
  );
}

export default MentionInput;
