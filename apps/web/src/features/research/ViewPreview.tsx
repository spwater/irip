/**
 * ViewPreview — ResearchView 静态图预览组件
 *
 * 展示高分辨率 PNG + 来源信息 + 版本缩略图列表
 */
import { useState, useEffect, useCallback } from 'react';
import { Card, Spin, Tag, Typography, Space, Button, Input, List, message } from 'antd';
import { EditOutlined } from '@ant-design/icons';
import {
  apiGetView,
  apiListViewVersions,
  apiUpdateViewMetadata,
  type ViewDetail,
  type ViewVersion,
} from '@/api/researchProducts';
import { getViewImageUrl } from '@/api/researchProducts';

const { Text } = Typography;
const { TextArea } = Input;

export type ViewPreviewProps = {
  workspaceId: string;
  viewId: string;
};

export function ViewPreview({ workspaceId, viewId }: ViewPreviewProps): JSX.Element {
  const [detail, setDetail] = useState<ViewDetail | null>(null);
  const [versions, setVersions] = useState<ViewVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState('');
  const [editCaption, setEditCaption] = useState('');

  const fetchDetail = useCallback(async () => {
    setLoading(true);
    try {
      const [detailRes, versionRes] = await Promise.all([
        apiGetView(workspaceId, viewId),
        apiListViewVersions(workspaceId, viewId),
      ]);
      setDetail(detailRes);
      setVersions(versionRes.items);
      setEditName(detailRes.name);
      setEditCaption(detailRes.caption ?? '');
    } catch {
      message.error('加载视图详情失败');
    } finally {
      setLoading(false);
    }
  }, [workspaceId, viewId]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  const handleSaveEdit = async () => {
    try {
      await apiUpdateViewMetadata(workspaceId, viewId, {
        name: editName,
        caption: editCaption,
      });
      message.success('已保存');
      setEditing(false);
      void fetchDetail();
    } catch {
      message.error('保存失败');
    }
  };

  if (loading || !detail) {
    return (
      <div style={{ textAlign: 'center', padding: 40 }}>
        <Spin />
      </div>
    );
  }

  const versionInfo = detail.current_version_info as Record<string, unknown> | null;
  const imageUrl = getViewImageUrl(workspaceId, viewId, detail.current_version);

  return (
    <div>
      {/* 头部 */}
      <Card size="small" style={{ marginBottom: 12 }}>
        {editing ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="名称" />
            <TextArea value={editCaption} onChange={(e) => setEditCaption(e.target.value)} placeholder="图注" rows={2} />
            <Space>
              <Button size="small" type="primary" onClick={handleSaveEdit}>保存</Button>
              <Button size="small" onClick={() => setEditing(false)}>取消</Button>
            </Space>
          </Space>
        ) : (
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space>
              <Text strong style={{ fontSize: 16 }}>{detail.name}</Text>
              <Tag>{detail.status}</Tag>
              <Tag color="blue">v{detail.current_version}</Tag>
            </Space>
            <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(true)}>编辑</Button>
          </Space>
        )}
        {detail.caption && !editing && (
          <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>{detail.caption}</Text>
        )}
      </Card>

      {/* 图片 */}
      <Card size="small" title="图表" style={{ marginBottom: 12 }}>
        <div style={{ textAlign: 'center' }}>
          <img
            src={imageUrl}
            alt={detail.name}
            style={{ maxWidth: '100%', maxHeight: 400, objectFit: 'contain' }}
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = 'none';
            }}
          />
        </div>
      </Card>

      {/* 来源信息 */}
      <Card size="small" title="来源信息" style={{ marginBottom: 12 }}>
        <Space direction="vertical" size="small">
          <Text style={{ fontSize: 12 }}>Run: {detail.source_run_id}</Text>
          {versionInfo && (
            <>
              {versionInfo.image_digest && (
                <Text style={{ fontSize: 12 }}>镜像: {String(versionInfo.image_digest)}</Text>
              )}
              {versionInfo.chart_description && (
                <Text style={{ fontSize: 12 }}>说明: {String(versionInfo.chart_description)}</Text>
              )}
            </>
          )}
        </Space>
      </Card>

      {/* 版本历史 */}
      {versions.length > 0 && (
        <Card size="small" title="版本历史" style={{ marginBottom: 12 }}>
          <List
            size="small"
            dataSource={versions}
            renderItem={(v) => (
              <List.Item>
                <Space>
                  <Tag>v{v.version_number}</Tag>
                  <Text style={{ fontSize: 12 }}>{v.image_format}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {new Date(v.created_at).toLocaleString()}
                  </Text>
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}
    </div>
  );
}
