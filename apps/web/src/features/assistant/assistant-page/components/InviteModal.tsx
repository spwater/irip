/**
 * InviteModal — 成员管理 Modal（邀请 + 移除一体）。
 *
 * 从 AssistantPage.tsx 提取。irip-ai-collab 协作功能。
 */

import {
  Modal,
  Select,
  Typography,
} from 'antd';
import type { InviteModalProps } from '../types';

const { Text } = Typography;

export function InviteModal(props: InviteModalProps): JSX.Element {
  const {
    open,
    inviteUserIds,
    setInviteUserIds,
    mentionableUsersData,
    currentUser,
    onOk,
    onCancel,
  } = props;

  return (
    <Modal
      title="管理对话成员"
      open={open}
      onOk={onOk}
      onCancel={onCancel}
      okText="保存"
      cancelText="取消"
      width={480}
    >
      <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
        勾选要加入的成员，取消勾选移除成员。仅限同组织活跃用户。
      </Text>
      <Select
        mode="multiple"
        placeholder="选择成员"
        style={{ width: '100%' }}
        value={inviteUserIds}
        onChange={setInviteUserIds}
        showSearch
        optionFilterProp="label"
        options={(mentionableUsersData ?? [])
          .filter((u) => u.id !== currentUser?.id)
          .map((u) => ({ value: u.id, label: `${u.display_name}${u.roles.length > 0 ? ` (${u.roles.join(', ')})` : ''}` }))}
        notFoundContent="无可选用户"
      />
    </Modal>
  );
}

export default InviteModal;
