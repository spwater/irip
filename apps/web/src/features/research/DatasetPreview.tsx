/**
 * DatasetPreview — DerivedDataset 三段式数据预览组件
 *
 * 展示 metadata / points 表格 / series 表格 + field_manifest
 */
import { useState, useEffect, useCallback } from 'react';
import { Card, Spin, Table, Tag, Typography, Space, Button, Input, message } from 'antd';
import { EditOutlined } from '@ant-design/icons';
import { apiGetDataset, apiUpdateDatasetMetadata, type DatasetDetail } from '@/api/researchProducts';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

export type DatasetPreviewProps = {
  workspaceId: string;
  datasetId: string;
};

export function DatasetPreview({ workspaceId, datasetId }: DatasetPreviewProps): JSX.Element {
  const [detail, setDetail] = useState<DatasetDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState('');
  const [editSummary, setEditSummary] = useState('');

  const fetchDetail = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGetDataset(workspaceId, datasetId);
      setDetail(res);
      setEditName(res.name);
      setEditSummary(res.summary ?? '');
    } catch {
      message.error('加载数据集详情失败');
    } finally {
      setLoading(false);
    }
  }, [workspaceId, datasetId]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  const handleSaveEdit = async () => {
    try {
      await apiUpdateDatasetMetadata(workspaceId, datasetId, {
        name: editName,
        summary: editSummary,
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

  const versionData = detail.current_version_data as Record<string, unknown> | null;
  const metadata = (versionData?.metadata_content as Record<string, unknown>) ?? {};
  const points = (versionData?.points_content as Array<Record<string, unknown>>) ?? [];
  const series = (versionData?.series_content as Array<Record<string, unknown>>) ?? [];
  const fieldManifest = (versionData?.field_manifest as Array<Record<string, unknown>>) ?? [];

  return (
    <div>
      {/* 头部 */}
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {editing ? (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="名称" />
              <TextArea value={editSummary} onChange={(e) => setEditSummary(e.target.value)} placeholder="摘要" rows={2} />
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
          {detail.summary && !editing && (
            <Text type="secondary">{detail.summary}</Text>
          )}
          {detail.tags.length > 0 && (
            <Space size="small" wrap>
              {detail.tags.map((tag) => (
                <Tag key={tag}>{tag}</Tag>
              ))}
            </Space>
          )}
        </Space>
      </Card>

      {/* 来源 */}
      <Card size="small" title="来源" style={{ marginBottom: 12 }}>
        <Space direction="vertical" size="small">
          <Text style={{ fontSize: 12 }}>Run: {detail.source_run_id}</Text>
          {detail.source_snapshot_id && (
            <Text style={{ fontSize: 12 }}>Snapshot: {detail.source_snapshot_id}</Text>
          )}
        </Space>
      </Card>

      {/* 数据预览 */}
      <Card size="small" title={`数据预览 (v${detail.current_version})`} style={{ marginBottom: 12 }}>
        {/* metadata */}
        <div style={{ marginBottom: 12 }}>
          <Text strong>📋 metadata</Text>
          <Paragraph style={{ whiteSpace: 'pre-wrap', fontSize: 13, marginTop: 4 }}>
            {JSON.stringify(metadata, null, 2)}
          </Paragraph>
        </div>

        {/* points */}
        {points.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Text strong>📊 points ({points.length})</Text>
            <Table
              size="small"
              style={{ marginTop: 8 }}
              dataSource={points.map((pt, i) => ({ ...pt, key: i }))}
              columns={[
                { title: '指标名', dataIndex: 'name', key: 'name' },
                { title: '值', dataIndex: 'value', key: 'value' },
                { title: '单位', dataIndex: 'unit', key: 'unit' },
              ]}
              pagination={false}
            />
          </div>
        )}

        {/* series */}
        {series.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Text strong>📈 series ({series.length})</Text>
            {series.map((sr, i) => {
              const srName = String(sr.name ?? '');
              const columns = (sr.columns as string[]) ?? [];
              const rows = (sr.rows as Array<Record<string, unknown>>) ?? [];
              const tableColumns = columns.map((col) => ({
                title: col,
                dataIndex: col,
                key: col,
              }));
              return (
                <div key={i} style={{ marginTop: 8 }}>
                  <Text style={{ fontSize: 12 }}>{srName} ({rows.length}行 × {columns.length}列)</Text>
                  <Table
                    size="small"
                    style={{ marginTop: 4 }}
                    dataSource={rows.map((row, ri) => ({ ...row, key: ri }))}
                    columns={tableColumns}
                    pagination={{ pageSize: 5, size: 'small' }}
                    scroll={{ x: true }}
                  />
                </div>
              );
            })}
          </div>
        )}

        {/* field_manifest */}
        {fieldManifest.length > 0 && (
          <div>
            <Text strong>📝 field_manifest</Text>
            <Table
              size="small"
              style={{ marginTop: 8 }}
              dataSource={fieldManifest.map((fm, i) => ({ ...fm, key: i }))}
              columns={[
                { title: '字段名', dataIndex: 'field_name', key: 'field_name' },
                { title: '类型', dataIndex: 'inferred_type', key: 'inferred_type' },
                { title: '单位', dataIndex: 'unit', key: 'unit' },
                { title: '说明', dataIndex: 'description', key: 'description' },
              ]}
              pagination={false}
            />
          </div>
        )}
      </Card>
    </div>
  );
}
