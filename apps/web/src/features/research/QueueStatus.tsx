/**
 * QueueStatus — 排队 UI 组件
 *
 * 功能：
 * - 排队位置显示（"第 3 位"）
 * - 前方用户数（"前方 2 位"）
 * - 预计等待时间（"~8 分钟"）
 * - 队列进度示意条
 * - 取消排队按钮 → apiCancelRun
 * - 位置实时更新（通过 SSE 事件或 5 秒轮询 apiGetQueueStatus）
 */

import { useEffect, useState, useCallback } from 'react';
import { Button, Progress, Spin, Space, Typography } from 'antd';
import { ClockCircleOutlined, UserOutlined } from '@ant-design/icons';
import { apiGetQueueStatus, apiCancelRun, type QueueStatus as QueueStatusType } from '../../api/research';

export type QueueStatusProps = {
  workspaceId: string;
  runId: string;
  initialPosition?: number;
  onCancel: () => void;
  onCancelLoading?: boolean;
};

export function QueueStatus({
  workspaceId,
  runId,
  onCancel,
  onCancelLoading,
}: QueueStatusProps) {
  const [queueInfo, setQueueInfo] = useState<QueueStatusType | null>(null);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);

  const fetchQueueStatus = useCallback(async () => {
    try {
      const info = await apiGetQueueStatus(workspaceId, runId);
      setQueueInfo(info);
      setLoading(false);
      // 如果位置为 0，表示已出队
      if (info.position === 0) {
        window.location.reload();
      }
    } catch {
      setLoading(false);
    }
  }, [workspaceId, runId]);

  useEffect(() => {
    fetchQueueStatus();
    const timer = setInterval(fetchQueueStatus, 5000);
    return () => clearInterval(timer);
  }, [fetchQueueStatus]);

  const handleCancel = useCallback(async () => {
    setCancelling(true);
    try {
      await apiCancelRun(workspaceId, runId);
      onCancel();
    } catch {
      message.error('取消排队失败');
    } finally {
      setCancelling(false);
    }
  }, [workspaceId, runId, onCancel]);

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 0' }}>
        <Spin tip="加载排队状态..." />
      </div>
    );
  }

  if (!queueInfo || queueInfo.position === 0) {
    return null;
  }

  const estimatedMinutes = Math.ceil(queueInfo.estimated_wait_seconds / 60);
  const progressPercent = Math.max(10, 100 - queueInfo.position * 10);

  return (
    <div
      style={{
        textAlign: 'center',
        padding: '32px 24px',
        background: '#fafafa',
        borderRadius: 8,
      }}
    >
      <ClockCircleOutlined style={{ fontSize: 48, color: '#1890ff', marginBottom: 16 }} />

      <Typography.Title level={4} style={{ marginBottom: 24 }}>
        正在排队等待执行
      </Typography.Title>

      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <div>
          <Typography.Text strong style={{ fontSize: 24 }}>
            第 {queueInfo.position} 位
          </Typography.Text>
        </div>

        <div>
          <Space>
            <UserOutlined />
            <Typography.Text type="secondary">
              前方 {queueInfo.ahead_count} 位用户
            </Typography.Text>
          </Space>
        </div>

        <div>
          <Typography.Text type="secondary">
            预计等待 ~{estimatedMinutes} 分钟
          </Typography.Text>
        </div>

        <Progress
          percent={progressPercent}
          showInfo={false}
          strokeColor="#1890ff"
          trailColor="#f0f0f0"
        />

        <Button
          danger
          loading={cancelling || onCancelLoading}
          onClick={handleCancel}
          style={{ marginTop: 8 }}
        >
          取消排队
        </Button>
      </Space>
    </div>
  );
}
