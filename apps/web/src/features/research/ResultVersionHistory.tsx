/**
 * ResultVersionHistory — 版本历史组件
 *
 * 版本列表 + 版本状态展示
 */
import { useCallback, useEffect, useState } from 'react';
import { Card, List, Tag, Spin, Empty, Typography, Space, message } from 'antd';
import { HistoryOutlined, EyeOutlined } from '@ant-design/icons';
import {
  apiGetResultVersionDetail,
  apiGetPublicationVersion,
  type ResultVersionRef,
  type ResultVersionDetail,
} from '@/api/researchPublish';

const { Text } = Typography;

export type ResultVersionHistoryProps = {
  workspaceId?: string;
  resultId: string;
  versionHistory: ResultVersionRef[];
  onVersionSelect?: (version: ResultVersionDetail) => void;
};

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  active: { label: '活跃', color: 'green' },
  superseded: { label: '已替代', color: 'default' },
  withdrawn: { label: '已撤回', color: 'red' },
};

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function ResultVersionHistory({
  workspaceId,
  resultId,
  versionHistory,
  onVersionSelect,
}: ResultVersionHistoryProps): JSX.Element {
  const [loading, setLoading] = useState(false);
  const [loadingVersion, setLoadingVersion] = useState(false);

  const handleVersionClick = useCallback(
    async (versionNumber: number) => {
      if (!onVersionSelect) return;
      setLoadingVersion(true);
      try {
        let detail: ResultVersionDetail;
        if (workspaceId) {
          detail = await apiGetResultVersionDetail(workspaceId, resultId, versionNumber);
        } else {
          detail = await apiGetPublicationVersion(resultId, versionNumber);
        }
        onVersionSelect(detail);
      } catch {
        message.error('加载版本详情失败');
      } finally {
        setLoadingVersion(false);
      }
    },
    [workspaceId, resultId, onVersionSelect],
  );

  useEffect(() => {
    setLoading(true);
    const timer = setTimeout(() => setLoading(false), 100);
    return () => clearTimeout(timer);
  }, []);

  if (loading) {
    return (
      <Card size="small" title="版本历史" style={{ marginBottom: 12 }}>
        <div style={{ textAlign: 'center', padding: 12 }}>
          <Spin size="small" />
        </div>
      </Card>
    );
  }

  if (versionHistory.length === 0) {
    return (
      <Card size="small" title="版本历史" style={{ marginBottom: 12 }}>
        <Empty description="暂无版本" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </Card>
    );
  }

  // 按版本号倒序排列
  const sorted = [...versionHistory].sort((a, b) => b.version_number - a.version_number);

  return (
    <Card
      size="small"
      title={
        <Space>
          <HistoryOutlined />
          <Text strong>版本历史 ({sorted.length})</Text>
        </Space>
      }
      style={{ marginBottom: 12 }}
    >
      <List
        size="small"
        dataSource={sorted}
        loading={loadingVersion}
        renderItem={(v) => {
          const statusInfo = STATUS_LABELS[v.status] ?? { label: v.status, color: 'default' };
          return (
            <List.Item
              style={{ cursor: onVersionSelect ? 'pointer' : 'default', padding: '6px 0' }}
              onClick={() => handleVersionClick(v.version_number)}
            >
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Space>
                  <Tag color="blue" style={{ margin: 0 }}>v{v.version_number}</Tag>
                  <Text style={{ fontSize: 12 }} ellipsis={{ tooltip: v.title }}>
                    {v.title}
                  </Text>
                </Space>
                <Space size={4}>
                  <Tag color={statusInfo.color} style={{ margin: 0, fontSize: 10 }}>
                    {statusInfo.label}
                  </Tag>
                  <Text type="secondary" style={{ fontSize: 10 }}>
                    {formatTime(v.published_at)}
                  </Text>
                  {onVersionSelect && <EyeOutlined style={{ fontSize: 12, color: 'var(--ocean-text-muted, #8c8c8c)' }} />}
                </Space>
              </Space>
            </List.Item>
          );
        }}
      />
    </Card>
  );
}
