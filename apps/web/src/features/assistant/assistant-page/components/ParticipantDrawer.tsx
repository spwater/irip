/**
 * ParticipantDrawer — 参与者列表 Drawer。
 *
 * 从 AssistantPage.tsx 提取。irip-ai-collab 协作功能。
 */

import {
  Avatar,
  Button,
  Drawer,
  List,
  Space,
  Tag,
  Typography,
} from 'antd';
import type { ParticipantDrawerProps } from '../types';

const { Text } = Typography;

export function ParticipantDrawer(props: ParticipantDrawerProps): JSX.Element {
  const {
    open,
    onClose,
    participantsData,
    isOwner,
    selectedConvId,
    onRemoveParticipant,
  } = props;

  return (
    <Drawer
      title="对话参与者"
      open={open}
      onClose={onClose}
      width={320}
    >
      <List
        dataSource={participantsData ?? []}
        renderItem={(p) => (
          <List.Item
            actions={
              isOwner && p.role !== 'owner'
                ? [<Button key="remove" type="link" danger size="small"
                    onClick={async () => {
                      if (!selectedConvId) return;
                      onRemoveParticipant(selectedConvId, p.user_id);
                    }}>移除</Button>]
                : undefined
            }
          >
            <List.Item.Meta
              avatar={<Avatar src={p.avatar_url} style={{ backgroundColor: p.role === 'owner' ? '#faad14' : '#1686AE' }}>{p.display_name.charAt(0)}</Avatar>}
              title={<Space size={4}><Text>{p.display_name}</Text><Tag color={p.role === 'owner' ? 'gold' : 'blue'} style={{ fontSize: 10 }}>{p.role === 'owner' ? '创建者' : '成员'}</Tag></Space>}
              description={<Text type="secondary" style={{ fontSize: 12 }}>加入于 {new Date(p.joined_at).toLocaleDateString('zh-CN')}</Text>}
            />
          </List.Item>
        )}
        locale={{ emptyText: '暂无参与者' }}
      />
    </Drawer>
  );
}

export default ParticipantDrawer;
