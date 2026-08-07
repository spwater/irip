/**
 * CandidateDataCard — 候选数据卡片
 *
 * 展示三段式结构摘要 + 字段清单 + 来源步骤 + 确认按钮
 */
import { useState } from 'react';
import { Card, Tag, Button, Typography, Space, message } from 'antd';
import {
  DatabaseOutlined,
  CheckOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import { apiCreateDataset, type CandidateProduct } from '@/api/researchProducts';

const { Text } = Typography;

export type CandidateDataCardProps = {
  workspaceId: string;
  candidate: CandidateProduct;
  onConfirm: () => void;
};

export function CandidateDataCard({
  workspaceId,
  candidate,
  onConfirm,
}: CandidateDataCardProps): JSX.Element {
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  const preview = candidate.preview_data as Record<string, unknown>;
  const metadataKeys = (preview?.metadata_keys as string[]) ?? [];
  const pointsPreview = (preview?.points_preview as Array<Record<string, unknown>>) ?? [];
  const seriesPreview = (preview?.series_preview as Array<Record<string, unknown>>) ?? [];
  const fieldNames = (preview?.field_names as string[]) ?? [];
  const pointsCount = (preview?.points_count as number) ?? 0;
  const seriesCount = (preview?.series_count as number) ?? 0;
  const metadataPreview = (preview?.metadata_preview ?? {}) as Record<string, unknown>;

  const handleConfirm = async () => {
    if (!candidate.source_artifact_id) return;
    setConfirming(true);
    try {
      const name = metadataKeys.length > 0
        ? String(metadataPreview[metadataKeys[0]] ?? `候选数据 ${candidate.candidate_id.slice(0, 8)}`)
        : `候选数据 ${candidate.candidate_id.slice(0, 8)}`;
      await apiCreateDataset(workspaceId, {
        artifact_id: candidate.source_artifact_id,
        name,
      });
      message.success('数据集已确认');
      setConfirmed(true);
      onConfirm();
    } catch {
      message.error('确认失败');
    } finally {
      setConfirming(false);
    }
  };

  if (confirmed) {
    return (
      <Card size="small" style={{ marginBottom: 8, opacity: 0.6 }}>
        <Space>
          <CheckOutlined style={{ color: 'green' }} />
          <Text>已确认</Text>
        </Space>
      </Card>
    );
  }

  if (candidate.status === 'unavailable') {
    return (
      <Card size="small" style={{ marginBottom: 8, borderColor: 'orange' }}>
        <Space direction="vertical" size="small">
          <Space>
            <DatabaseOutlined />
            <Text type="secondary">候选数据（不可用）</Text>
          </Space>
          <Text type="danger" style={{ fontSize: 12 }}>
            {candidate.error_reason || '校验失败'}
          </Text>
        </Space>
      </Card>
    );
  }

  return (
    <Card
      size="small"
      style={{ marginBottom: 8 }}
      actions={[
        <Button
          key="confirm"
          type="primary"
          size="small"
          icon={confirming ? <LoadingOutlined /> : <CheckOutlined />}
          loading={confirming}
          onClick={handleConfirm}
        >
          确认
        </Button>,
      ]}
    >
      <Space direction="vertical" size="small" style={{ width: '100%' }}>
        <Space>
          <DatabaseOutlined />
          <Text strong style={{ fontSize: 13 }}>
            {metadataKeys.length > 0
              ? String(metadataPreview[metadataKeys[0]] ?? '候选数据')
              : '候选数据'}
          </Text>
        </Space>

        {metadataKeys.length > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            metadata: {metadataKeys.join(', ')}
          </Text>
        )}

        {pointsCount > 0 && (
          <div style={{ fontSize: 12 }}>
            <Text type="secondary">points ({pointsCount}): </Text>
            {pointsPreview.slice(0, 3).map((pt, i) => (
              <Tag key={i} style={{ fontSize: 10 }}>
                {String(pt.name)}: {String(pt.value)}
                {pt.unit ? ` ${pt.unit}` : ''}
              </Tag>
            ))}
          </div>
        )}

        {seriesCount > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            series ({seriesCount}): {seriesPreview.map((s) => `${s.name} (${s.row_count}行×${s.column_count}列)`).join(', ')}
          </Text>
        )}

        {fieldNames.length > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            字段: {fieldNames.join(', ')}
          </Text>
        )}

        <Text type="secondary" style={{ fontSize: 11 }}>
          来源: Step {candidate.step_name} [{candidate.step_status}]
        </Text>
      </Space>
    </Card>
  );
}
