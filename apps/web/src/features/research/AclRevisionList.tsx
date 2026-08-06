/**
 * AclRevisionList — 权限变更记录组件
 *
 * ACL Revision 历史（前后值 + 操作者 + 时间 + 原因 + declassify 标记）
 */
import { Card, Tag, Typography, Space, Empty, Timeline, Tooltip } from 'antd';
import {
  SafetyOutlined,
  LockOutlined,
  GlobalOutlined,
  TeamOutlined,
  UserOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { AclRevisionRef } from '@/api/researchPublish';

const { Text } = Typography;

export type AclRevisionListProps = {
  revisions: AclRevisionRef[];
};

const ACL_LABELS: Record<string, { label: string; color: string; icon: JSX.Element }> = {
  private: { label: '私有', color: 'default', icon: <LockOutlined /> },
  tree: { label: '部门', color: 'blue', icon: <TeamOutlined /> },
  explicit: { label: '指定用户', color: 'orange', icon: <UserOutlined /> },
  all: { label: '公开', color: 'green', icon: <GlobalOutlined /> },
};

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function formatUserId(id: string): string {
  if (!id) return '—';
  return id.substring(0, 8) + '…';
}

export function AclRevisionList({ revisions }: AclRevisionListProps): JSX.Element {
  if (revisions.length === 0) {
    return (
      <Card
        size="small"
        title={
          <Space>
            <SafetyOutlined />
            <Text strong>权限变更记录</Text>
          </Space>
        }
        style={{ marginBottom: 12 }}
      >
        <Empty description="暂无权限变更记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </Card>
    );
  }

  // 按版本号倒序排列（最新在前）
  const sorted = [...revisions].sort((a, b) => b.revision_number - a.revision_number);

  return (
    <Card
      size="small"
      title={
        <Space>
          <SafetyOutlined />
          <Text strong>权限变更记录 ({sorted.length})</Text>
        </Space>
      }
      style={{ marginBottom: 12 }}
    >
      <Timeline
        items={sorted.map((rev) => {
          const aclInfo = ACL_LABELS[rev.acl_type] ?? ACL_LABELS.private;
          const prevAclInfo = rev.previous_acl_type
            ? ACL_LABELS[rev.previous_acl_type] ?? { label: rev.previous_acl_type, color: 'default', icon: <LockOutlined /> }
            : null;

          return {
            color: rev.is_declassify ? 'red' : 'blue',
            dot: rev.is_declassify ? <WarningOutlined style={{ color: '#ff4d4f' }} /> : undefined,
            children: (
              <div style={{ paddingBottom: 8 }}>
                {/* Revision number + ACL transition */}
                <Space size={6} style={{ marginBottom: 4 }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    #{rev.revision_number}
                  </Text>
                  {prevAclInfo ? (
                    <>
                      <Tag style={{ fontSize: 10, margin: 0 }}>
                        {prevAclInfo.icon} {prevAclInfo.label}
                      </Tag>
                      <Text type="secondary" style={{ fontSize: 11 }}>→</Text>
                    </>
                  ) : null}
                  <Tag color={aclInfo.color} style={{ fontSize: 10, margin: 0 }}>
                    {aclInfo.icon} {aclInfo.label}
                  </Tag>
                  {rev.is_declassify && (
                    <Tag color="red" style={{ fontSize: 10, margin: 0 }}>
                      <WarningOutlined /> 突破权限包络
                    </Tag>
                  )}
                </Space>

                {/* Explicit user IDs */}
                {rev.explicit_user_ids && rev.explicit_user_ids.length > 0 && (
                  <div style={{ fontSize: 11, marginBottom: 2 }}>
                    <Text type="secondary">指定用户: </Text>
                    <Tooltip title={rev.explicit_user_ids.join(', ')}>
                      <Text style={{ fontSize: 11 }}>
                        {rev.explicit_user_ids.map(formatUserId).join(', ')}
                      </Text>
                    </Tooltip>
                  </div>
                )}

                {/* Changed by + time */}
                <div style={{ fontSize: 11, color: 'var(--ocean-text-muted, #8c8c8c)' }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    操作者: {formatUserId(rev.changed_by)}
                  </Text>
                  <span style={{ margin: '0 8px' }}>·</span>
                  {formatTime(rev.changed_at)}
                </div>

                {/* Change reason */}
                {rev.change_reason && (
                  <div style={{ fontSize: 11, marginTop: 2 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>原因: </Text>
                    <Text style={{ fontSize: 11 }}>{rev.change_reason}</Text>
                  </div>
                )}

                {/* Declassify reason */}
                {rev.is_declassify && rev.declassify_reason && (
                  <div style={{ fontSize: 11, marginTop: 2, color: '#ff4d4f' }}>
                    <Text type="danger" style={{ fontSize: 11 }}>突破理由: </Text>
                    <Text style={{ fontSize: 11 }}>{rev.declassify_reason}</Text>
                  </div>
                )}
              </div>
            ),
          };
        })}
      />
    </Card>
  );
}
