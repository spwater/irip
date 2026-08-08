import { useState } from 'react';
import {
  Alert,
  Avatar,
  Button,
  Drawer,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { CrownOutlined, UserAddOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  apiInviteParticipant,
  apiListParticipants,
  apiListMentionableUsers,
  apiRemoveParticipant,
  type Participant,
} from '@/api/collaboration';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { extractApiError } from '@/api/types';

const { Text } = Typography;

/**
 * 对话参与者面板（irip-ai-collab）。
 *
 * 对话头部显示参与者头像组（最多 3 个 + +N），
 * 点击展开 Drawer 成员列表。
 * owner 可见「邀请成员」按钮和「移除」按钮。
 */
export function ParticipantPanel({
  conversationId,
  isOwner,
}: {
  conversationId: string | null;
  /** 当前用户是否为对话 owner */
  isOwner: boolean;
}): JSX.Element | null {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [inviteModalOpen, setInviteModalOpen] = useState(false);
  const [inviteUserId, setInviteUserId] = useState<string | undefined>(undefined);

  // 查询参与者列表
  const { data: participants } = useQuery({
    queryKey: ['participants', conversationId],
    queryFn: () => apiListParticipants(conversationId!),
    enabled: !!conversationId,
    retry: false,
  });

  // 查询可邀请用户（同 org）
  const { data: mentionableUsers } = useQuery({
    queryKey: ['mentionable-users'],
    queryFn: apiListMentionableUsers,
    enabled: inviteModalOpen,
    staleTime: 60_000,
  });

  // 邀请 mutation
  const inviteMutation = useMutation({
    mutationFn: (userId: string) => apiInviteParticipant(conversationId!, userId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['participants', conversationId] });
      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
      setInviteModalOpen(false);
      setInviteUserId(undefined);
      message.success('成员邀请成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // 移除 mutation
  const removeMutation = useMutation({
    mutationFn: (params: { userId: string }) =>
      apiRemoveParticipant(conversationId!, params.userId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['participants', conversationId] });
      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] });
      message.success('成员已移除');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  if (!conversationId) return null;

  const participantList: Participant[] = participants ?? [];
  const maxAvatars = 3;
  const shownAvatars = participantList.slice(0, maxAvatars);
  const extraCount = participantList.length - maxAvatars;

  // 可邀请用户（排除已是参与者）
  const existingIds = new Set(participantList.map((p) => p.user_id));
  const invitableUsers = (mentionableUsers ?? []).filter(
    (u) => !existingIds.has(u.id) && u.id !== user?.id,
  );

  return (
    <>
      {/* 头像组 + 邀请入口 */}
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', padding: '2px 8px', borderRadius: 4, background: 'rgba(22, 134, 174, 0.06)' }}
        onClick={() => setDrawerOpen(true)}
        role="button"
        tabIndex={0}
        aria-label="查看对话参与者"
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setDrawerOpen(true);
          }
        }}
      >
        <Avatar.Group size="small" maxCount={maxAvatars}>
          {shownAvatars.map((p) => (
            <Avatar
              key={p.user_id}
              src={p.avatar_url}
              size={24}
              style={{
                backgroundColor: p.role === 'owner' ? '#faad14' : '#1686AE',
                fontSize: 11,
              }}
            >
              {p.display_name.charAt(0)}
            </Avatar>
          ))}
        </Avatar.Group>
        {extraCount > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            +{extraCount}
          </Text>
        )}
        <Text type="secondary" style={{ fontSize: 12 }}>
          {participantList.length}人
        </Text>
        {isOwner && (
          <Button
            type="link"
            size="small"
            icon={<UserAddOutlined />}
            onClick={(e) => { e.stopPropagation(); setInviteModalOpen(true); }}
            style={{ padding: '0 4px', height: 'auto', fontSize: 12 }}
          >
            邀请
          </Button>
        )}
      </div>

      {/* 成员列表 Drawer */}
      <Drawer
        title="对话参与者"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={360}
      >
        {isOwner && (
          <div style={{ marginBottom: 16 }}>
            <Button
              type="primary"
              icon={<UserAddOutlined />}
              onClick={() => setInviteModalOpen(true)}
              size="small"
            >
              邀请成员
            </Button>
          </div>
        )}
        <List
          dataSource={participantList}
          renderItem={(p) => (
            <List.Item
              actions={
                isOwner && p.role !== 'owner'
                  ? [
                      <Popconfirm
                        key="remove"
                        title="确定移除该成员？"
                        onConfirm={() => removeMutation.mutate({ userId: p.user_id })}
                        okText="移除"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                      >
                        <Button type="link" danger size="small">
                          移除
                        </Button>
                      </Popconfirm>,
                    ]
                  : undefined
              }
            >
              <List.Item.Meta
                avatar={
                  <Avatar src={p.avatar_url} style={{ backgroundColor: p.role === 'owner' ? '#faad14' : '#1686AE' }}>
                    {p.role === 'owner' ? <CrownOutlined /> : p.display_name.charAt(0)}
                  </Avatar>
                }
                title={
                  <Space size={4}>
                    <Text>{p.display_name}</Text>
                    <Tag
                      color={p.role === 'owner' ? 'gold' : 'blue'}
                      style={{ fontSize: 10, margin: 0, padding: '0 4px' }}
                    >
                      {p.role === 'owner' ? '创建者' : '成员'}
                    </Tag>
                  </Space>
                }
                description={
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    加入于 {new Date(p.joined_at).toLocaleDateString('zh-CN')}
                  </Text>
                }
              />
            </List.Item>
          )}
          locale={{ emptyText: '暂无参与者' }}
        />
      </Drawer>

      {/* 邀请成员 Modal */}
      <Modal
        title="邀请成员"
        open={inviteModalOpen}
        onOk={() => {
          if (!inviteUserId) {
            message.warning('请选择要邀请的用户');
            return;
          }
          inviteMutation.mutate(inviteUserId);
        }}
        onCancel={() => {
          setInviteModalOpen(false);
          setInviteUserId(undefined);
        }}
        confirmLoading={inviteMutation.isPending}
        okText="邀请"
        cancelText="取消"
      >
        <Alert
          type="warning"
          showIcon
          message="副本语义提示"
          description="会话内的数据副本将随之暴露给新参与者，此操作不可撤销。"
          style={{ marginBottom: 12 }}
        />
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
          仅可邀请同组织内的活跃用户
        </Text>
        <Select
          placeholder="选择用户"
          style={{ width: '100%' }}
          value={inviteUserId}
          onChange={setInviteUserId}
          showSearch
          optionFilterProp="label"
          options={invitableUsers.map((u) => ({
            value: u.id,
            label: `${u.display_name}${u.roles.length > 0 ? ` (${u.roles.join(', ')})` : ''}`,
          }))}
          notFoundContent="无可邀请的用户"
        />
      </Modal>
    </>
  );
}

export default ParticipantPanel;
