/**
 * CandidateChartCard — 候选图表卡片
 *
 * 展示 PNG 缩略图 + 绑定信息 + 确认按钮
 */
import { useState } from 'react';
import { Card, Tag, Button, Typography, Space, message } from 'antd';
import {
  BarChartOutlined,
  CheckOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import { apiCreateView, type CandidateProduct } from '@/api/researchProducts';

const { Text } = Typography;

export type CandidateChartCardProps = {
  workspaceId: string;
  candidate: CandidateProduct;
  onConfirm: () => void;
};

export function CandidateChartCard({
  workspaceId,
  candidate,
  onConfirm,
}: CandidateChartCardProps): JSX.Element {
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  const preview = candidate.preview_data as Record<string, unknown>;
  const artifactKey = (preview?.artifact_key as string) ?? '';
  const imageFormat = (preview?.image_format as string) ?? 'png';

  const handleConfirm = async () => {
    if (!candidate.source_artifact_id) return;
    setConfirming(true);
    try {
      await apiCreateView(workspaceId, {
        artifact_id: candidate.source_artifact_id,
        name: artifactKey || `候选图表 ${candidate.candidate_id.slice(0, 8)}`,
      });
      message.success('视图已确认');
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
          <BarChartOutlined />
          <Text strong style={{ fontSize: 13 }}>{artifactKey || '候选图表'}</Text>
        </Space>
        <div style={{
          height: 80,
          background: '#f5f5f5',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 4,
        }}>
          <BarChartOutlined style={{ fontSize: 32, color: '#999' }} />
        </div>
        <Space size="small">
          <Tag style={{ fontSize: 10 }}>{imageFormat}</Tag>
          <Tag style={{ fontSize: 10 }}>{candidate.step_name}</Tag>
        </Space>
        <Text type="secondary" style={{ fontSize: 11 }}>
          来源: Step {candidate.step_name} [{candidate.step_status}]
        </Text>
      </Space>
    </Card>
  );
}
