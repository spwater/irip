/**
 * 组件详情面板。
 *
 * 从 ComponentsPage.tsx 拆出，包含：
 * - 组件基本信息展示（名称/版本/状态/时间）
 * - Manifest YAML 预览
 * - 版本历史列表 + 回滚功能
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Button,
  Descriptions,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  apiActivateVersion,
  apiListComponentVersions,
  type ComponentDetail,
  type ComponentVersionItem,
} from '@/api/equipment-flows';
import { extractApiError } from '@/api/types';
import { fmtTime, STATUS_COLOR, STATUS_LABEL } from './component-utils';

const { Title, Text } = Typography;

export function ComponentDetailPanel({
  detail,
  detailId,
  onVersionChange,
}: {
  detail: ComponentDetail;
  detailId: string;
  onVersionChange?: (versionId: string) => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const { data: versions, isLoading: versionsLoading } = useQuery({
    queryKey: ['component-versions', detailId],
    queryFn: () => apiListComponentVersions(detailId),
  });

  const rollbackMutation = useMutation({
    mutationFn: async (versionId: string) => {
      await apiActivateVersion(versionId);
      return versionId;
    },
    onSuccess: (versionId: string) => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      void queryClient.invalidateQueries({ queryKey: ['component', detailId] });
      void queryClient.invalidateQueries({ queryKey: ['component-versions', detailId] });
      onVersionChange?.(versionId);
      void queryClient.refetchQueries({ queryKey: ['component', versionId] });
      void queryClient.refetchQueries({ queryKey: ['component-versions', versionId] });
      message.success('已回滚到该版本');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  return (
    <div>
      <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="名称">
          <Text strong>{detail.display_name || detail.name}</Text>
          {detail.display_name && (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              {detail.name}
            </Text>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="版本">{detail.version}</Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={STATUS_COLOR[detail.status] ?? 'default'}>
            {STATUS_LABEL[detail.status] ?? detail.status}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="发布时间">
          {fmtTime(detail.published_at)}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {fmtTime(detail.created_at)}
        </Descriptions.Item>
      </Descriptions>

      <Title level={5} style={{ marginTop: 24 }}>
        Manifest (YAML)
      </Title>
      <pre
        style={{
          background: 'var(--ocean-surface-structural)',
          padding: 16,
          borderRadius: 6,
          fontSize: 13,
          fontFamily: 'var(--ocean-font-mono)',
          overflow: 'auto',
          maxHeight: 320,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {detail.manifest_yaml}
      </pre>

      <Title level={5} style={{ marginTop: 24 }}>
        版本历史
      </Title>
      {versionsLoading ? (
        <div style={{ textAlign: 'center', padding: 16 }}>
          <Spin size="small" />
        </div>
      ) : versions && versions.length > 0 ? (
        <div style={{ maxHeight: 300, overflow: 'auto' }}>
          {versions.map((v: ComponentVersionItem, idx: number) => {
            const isCurrent = detail.active_version_id ? v.id === detail.active_version_id : idx === 0;
            return (
              <div
                key={v.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '8px 12px',
                  borderBottom: '1px solid var(--ocean-border-subtle)',
                  background: isCurrent ? 'rgba(20, 118, 94, 0.06)' : 'transparent',
                }}
              >
                <Space size={8}>
                  <Tag color={isCurrent ? 'green' : 'default'}>
                    v{v.version}
                  </Tag>
                  {isCurrent && (
                    <Text type="success" style={{ fontSize: 11 }}>
                      当前
                    </Text>
                  )}
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {fmtTime(v.created_at)}
                  </Text>
                </Space>
                {!isCurrent && (
                  <Popconfirm
                    title={`回滚到 v${v.version}？`}
                    description="将恢复该版本的 manifest 为当前活跃版本"
                    onConfirm={() => rollbackMutation.mutate(v.id)}
                    okText="回滚"
                    cancelText="取消"
                  >
                    <Button
                      type="link"
                      size="small"
                      loading={rollbackMutation.isPending}
                    >
                      回滚
                    </Button>
                  </Popconfirm>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }}>
          暂无其他版本
        </Text>
      )}
    </div>
  );
}
