/**
 * PermissionEnvelopeView — 权限包络计算结果展示
 *
 * 源数据权限交集 vs 请求范围 vs 有效范围
 * 在发布确认弹窗和详情页中展示
 */
import { Card, Tag, Typography, Space, Empty, Alert, Tooltip, Descriptions } from 'antd';
import {
  LockOutlined,
  GlobalOutlined,
  TeamOutlined,
  UserOutlined,
  SafetyOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons';

const { Text, Paragraph } = Typography;

export type PermissionEnvelopeViewProps = {
  /** 发布时计算的权限包络快照（来自 ResultVersionDetail.published_permission_envelope） */
  envelope: Record<string, unknown> | null;
  /** 请求的 ACL 类型 */
  requestedAcl?: string;
  /** 当前有效 ACL 类型 */
  effectiveAcl?: string;
  /** 是否在发布确认弹窗中显示 */
  compact?: boolean;
};

const ACL_LABELS: Record<string, { label: string; color: string; icon: JSX.Element }> = {
  private: { label: '私有', color: 'default', icon: <LockOutlined /> },
  tree: { label: '部门', color: 'blue', icon: <TeamOutlined /> },
  explicit: { label: '指定用户', color: 'orange', icon: <UserOutlined /> },
  all: { label: '公开', color: 'green', icon: <GlobalOutlined /> },
};

function getAclTag(aclType: string | undefined): JSX.Element {
  if (!aclType) return <Tag>未知</Tag>;
  const info = ACL_LABELS[aclType] ?? { label: aclType, color: 'default', icon: <LockOutlined /> };
  return (
    <Tag color={info.color}>
      {info.icon} {info.label}
    </Tag>
  );
}

/**
 * 从 envelope 对象中安全提取信息
 */
function parseEnvelope(envelope: Record<string, unknown> | null): {
  aclType: string | null;
  explicitUserIds: string[];
  sourceDetails: Array<Record<string, unknown>>;
} {
  if (!envelope) return { aclType: null, explicitUserIds: [], sourceDetails: [] };
  return {
    aclType: (envelope.acl_type as string) ?? null,
    explicitUserIds: (envelope.explicit_user_ids as string[]) ?? [],
    sourceDetails: (envelope.source_details as Array<Record<string, unknown>>) ?? [],
  };
}

export function PermissionEnvelopeView({
  envelope,
  requestedAcl,
  effectiveAcl,
  compact,
}: PermissionEnvelopeViewProps): JSX.Element {
  const parsed = parseEnvelope(envelope);

  if (compact) {
    // 紧凑模式（用于发布确认弹窗中）
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Space>
          <Text type="secondary" style={{ fontSize: 12 }}>权限包络:</Text>
          {parsed.aclType ? getAclTag(parsed.aclType) : <Text type="secondary">未计算</Text>}
        </Space>
        {requestedAcl && (
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>请求范围:</Text>
            {getAclTag(requestedAcl)}
          </Space>
        )}
        {effectiveAcl && effectiveAcl !== requestedAcl && (
          <Alert
            type="warning"
            showIcon
            icon={<InfoCircleOutlined />}
            message={
              <span style={{ fontSize: 12 }}>
                有效范围为 <strong>{getAclTag(effectiveAcl)}</strong>（受权限包络约束）
              </span>
            }
            style={{ padding: '4px 8px' }}
          />
        )}
      </div>
    );
  }

  return (
    <Card
      size="small"
      title={
        <Space>
          <SafetyOutlined />
          <Text strong>权限包络</Text>
        </Space>
      }
      style={{ marginBottom: 12 }}
    >
      {!envelope ? (
        <Empty description="暂无权限包络信息" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {/* 源数据权限交集 */}
          <div>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
              源数据权限交集（权限包络）
            </Text>
            <Space size={6} wrap>
              {parsed.aclType && getAclTag(parsed.aclType)}
              {parsed.explicitUserIds.length > 0 && (
                <Tooltip title={parsed.explicitUserIds.join(', ')}>
                  <Tag style={{ fontSize: 11 }}>
                    <UserOutlined /> {parsed.explicitUserIds.length} 个指定用户
                  </Tag>
                </Tooltip>
              )}
            </Space>
          </div>

          {/* 源数据明细 */}
          {parsed.sourceDetails.length > 0 && (
            <div>
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                源数据权限明细
              </Text>
              <Descriptions
                size="small"
                column={1}
                bordered
                items={parsed.sourceDetails.map((src, idx) => ({
                  key: idx,
                  label: String(src.source_name ?? src.snapshot_id ?? `源 ${idx + 1}`),
                  children: (
                    <Space size={4}>
                      {getAclTag(String(src.acl_type ?? 'private'))}
                    </Space>
                  ),
                }))}
              />
            </div>
          )}

          {/* 请求范围 vs 有效范围 */}
          {requestedAcl && (
            <div>
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                请求范围
              </Text>
              {getAclTag(requestedAcl)}
            </div>
          )}
          {effectiveAcl && effectiveAcl !== requestedAcl && (
            <Alert
              type="info"
              showIcon
              message="权限包络约束"
              description={
                <Paragraph style={{ margin: 0, fontSize: 12 }}>
                  请求的 ACL 范围超出源数据权限包络交集，有效范围为{' '}
                  {getAclTag(effectiveAcl)}。
                  如需扩大可见范围，需通过 declassify 操作并提交理由。
                </Paragraph>
              }
              style={{ marginTop: 4 }}
            />
          )}
        </Space>
      )}
    </Card>
  );
}
