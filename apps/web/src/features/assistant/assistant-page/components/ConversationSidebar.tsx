/**
 * ConversationSidebar — 左侧对话列表侧栏。
 *
 * 从 AssistantPage.tsx 提取。包含：搜索栏、标签筛选、对话列表、
 * 置顶/归档/删除操作、新建对话按钮。
 */

import {
  Avatar,
  Button,
  Card,
  List,
  Popconfirm,
  Space,
  Tag,
  Typography,
} from 'antd';
import type { ConversationSummary } from '@/api/models-ai';
import { ConversationSearch } from '@/features/assistant/ConversationSearch';
import { ConversationTabs } from '@/features/assistant/ConversationTabs';
import type { ConversationSidebarProps } from '../types';

const { Title, Text } = Typography;

export function ConversationSidebar(props: ConversationSidebarProps): JSX.Element {
  const {
    showArchived,
    setShowArchived,
    setSearchKeyword,
    activeTab,
    setActiveTab,
    conversationList,
    selectedConvId,
    setSelectedConvId,
    onNewConversation,
    onTogglePin,
    onToggleArchive,
    onDeleteConversation,
  } = props;

  return (
    <Card
      size="small"
      style={{ width: 260, display: 'flex', flexDirection: 'column', flexShrink: 0 }}
      styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' } }}
      title={
        <Space size={4}>
          <Title level={5} style={{ margin: 0 }}>
            {showArchived ? '归档对话' : '对话列表'}
          </Title>
          <Button
            type="link"
            size="small"
            style={{ padding: '0 4px', fontSize: 12 }}
            onClick={() => setShowArchived(!showArchived)}
          >
            {showArchived ? '返回' : '归档'}
          </Button>
        </Space>
      }
      extra={
        showArchived ? null : (
        <Button
          type="primary"
          size="small"
          onClick={onNewConversation}
        >
          新建
        </Button>
        )
      }
    >
      <ConversationSearch onSearch={setSearchKeyword} />
      <ConversationTabs activeTab={activeTab} onTabChange={setActiveTab} />
      <div style={{ flex: 1, overflow: 'auto' }}>
      <List
        dataSource={conversationList}
        renderItem={(conv: ConversationSummary) => (
          <List.Item
            style={{
              padding: '8px 12px',
              cursor: 'pointer',
              position: 'relative',
              background: selectedConvId === conv.id ? 'rgba(22, 134, 174, 0.12)' : 'transparent',
              transition: 'background 0.2s',
            }}
            role="button"
            tabIndex={0}
            aria-label={`选择对话：${conv.title || '新对话'}`}
            aria-pressed={selectedConvId === conv.id}
            onClick={() => setSelectedConvId(conv.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setSelectedConvId(conv.id);
              }
            }}
          >
            <List.Item.Meta
              title={
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontWeight: selectedConvId === conv.id ? 600 : 400, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                    {conv.title || '新对话'}
                  </span>
                  <div style={{ display: 'flex', gap: 2, flexShrink: 0, marginLeft: 4 }}>
                    {conv.pinned && <Tag color='gold' style={{ fontSize: 9, margin: 0, padding: '0 4px', lineHeight: '16px' }}>置顶</Tag>}
                    {conv.archived && <Tag color='default' style={{ fontSize: 9, margin: 0, padding: '0 4px', lineHeight: '16px' }}>归档</Tag>}
                  </div>
                </div>
              }
              description={
                conv.participants && conv.participants.length > 1 ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                    <Avatar.Group size={20} maxCount={3}>
                      {conv.participants.slice(0, 3).map((p) => (
                        <Avatar
                          key={p.user_id}
                          src={p.avatar_url}
                          size={20}
                          style={{ fontSize: 10 }}
                        >
                          {p.display_name?.charAt(0) || '?'}
                        </Avatar>
                      ))}
                    </Avatar.Group>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {conv.participants.length}人协作
                    </Text>
                  </div>
                ) : undefined
              }
            />
            {/* hover 悬浮操作按钮 */}
            <div
              className="conv-actions"
              style={{
                position: 'absolute',
                right: 6,
                top: 6,
                display: 'flex',
                gap: 2,
                opacity: 0,
                transition: 'opacity 0.2s',
                background: selectedConvId === conv.id ? 'rgba(22, 134, 174, 0.12)' : 'var(--ocean-surface-strong)',
                padding: '2px 4px',
                borderRadius: 4,
                boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
              }}
            >
              <Button
                size="small"
                type="text"
                onClick={(e) => {
                  e.stopPropagation();
                  onTogglePin(conv.id);
                }}
                style={{ padding: '0 4px', fontSize: 12 }}
              >
                {conv.pinned ? '解除置顶' : '置顶'}
              </Button>
              <Button
                size="small"
                type="text"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleArchive(conv.id);
                }}
                style={{ padding: '0 4px', fontSize: 12 }}
              >
                {conv.archived ? '取消归档' : '归档'}
              </Button>
              {showArchived && (
                <Popconfirm
                  title="确认永久删除？"
                  description="删除后无法恢复该对话及其所有消息。"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => {
                    onDeleteConversation(conv.id);
                  }}
                >
                  <Button
                    size="small"
                    type="text"
                    danger
                    onClick={(e) => e.stopPropagation()}
                    style={{ padding: '0 4px', fontSize: 12, color: 'var(--ocean-status-danger)' }}
                  >
                    删除
                  </Button>
                </Popconfirm>
              )}
            </div>
          </List.Item>
        )}
        locale={{ emptyText: showArchived ? '暂无归档对话' : '暂无对话，直接输入消息即可开始' }}
      />
      </div>
    </Card>
  );
}

export default ConversationSidebar;
