import {
  Alert,
  Button,
  Descriptions,
  Space,
  Table,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiGetSystemHealth,
  type HealthCheck,
  type SystemHealth,
} from '@/api/client';
import { useAuthStore } from '@/auth/AuthProvider';
import { OceanPanel, StatusMark, FeedbackState } from '@/components/ui';
import type { StatusSemantic } from '@/theme/tokens';

const { Title, Text } = Typography;

/** 整体状态 → 语义映射 */
const STATUS_SEMANTIC: Record<string, StatusSemantic> = {
  ok: 'success',
  healthy: 'success',
  degraded: 'warning',
  not_ready: 'danger',
  error: 'danger',
};

/** 整体状态 → 中文标签映射 */
const STATUS_LABEL: Record<string, string> = {
  ok: '正常',
  healthy: '正常',
  degraded: '降级',
  not_ready: '未就绪',
  error: '异常',
};

/** 检查项状态 → 语义映射 */
const CHECK_SEMANTIC: Record<string, StatusSemantic> = {
  ok: 'success',
  healthy: 'success',
  error: 'danger',
  degraded: 'warning',
  warning: 'warning',
};

/** 检查项状态 → 中文标签映射 */
const CHECK_LABEL: Record<string, string> = {
  ok: '正常',
  healthy: '正常',
  error: '异常',
  degraded: '降级',
  warning: '警告',
};

/**
 * 系统健康页面（设计文档第 10.7 节 — 治理监控原型）
 *
 * 功能：
 * - 整体状态（ready / degraded / not_ready）
 * - 各检查项状态（名称 / 状态 / 延迟 / 消息）
 * - 迁移版本、Worker 心跳时间、Outbox 积压数
 */
export function SystemHealthPage(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const canView: boolean = user?.permissions?.includes('system:health') ?? true;

  // ---- 数据查询：系统健康 ----
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['system-health'],
    queryFn: apiGetSystemHealth,
    refetchInterval: 30000, // 每 30 秒自动刷新
    enabled: canView,
  });

  // ---- 权限检查 ----
  if (!canView) {
    return (
      <div>
        <Title level={5} style={{ color: 'var(--ocean-text-primary)' }}>系统健康</Title>
        <Text type="danger">您没有系统健康监控权限。</Text>
      </div>
    );
  }

  if (isLoading) {
    return <FeedbackState state="loading" title="加载系统健康状态…" style={{ padding: 48 }} />;
  }

  const health: SystemHealth | undefined = data;
  const status: string = health?.status ?? 'not_ready';
  const checks: HealthCheck[] = health?.checks ?? [];

  // ---- 检查项表格列 ----
  const columns: ColumnsType<HealthCheck> = [
    {
      title: '检查项',
      dataIndex: 'name',
      key: 'name',
      width: 200,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (val: string) => (
        <StatusMark
          semantic={CHECK_SEMANTIC[val] ?? 'neutral'}
          label={CHECK_LABEL[val] ?? val}
        />
      ),
    },
    {
      title: '延迟 (ms)',
      dataIndex: 'latency_ms',
      key: 'latency_ms',
      width: 120,
      align: 'center' as const,
      render: (val: number | null) =>
        val !== null && val !== undefined ? (
          <span className="ocean-tabular-nums">{val}ms</span>
        ) : '-',
    },
    {
      title: '消息',
      dataIndex: 'message',
      key: 'message',
      render: (val: string | null) => val ?? <Text type="secondary">-</Text>,
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={5} style={{ margin: 0, paddingTop: '1em', color: 'var(--ocean-text-primary)' }}>系统健康</Title>
        <Space>
          <Button onClick={() => refetch()} loading={isFetching}>
            刷新
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            每 30 秒自动刷新
          </Text>
        </Space>
      </div>

      {/* 整体状态提示 */}
      {status !== 'ok' && status !== 'healthy' && (
        <Alert
          style={{ marginBottom: 16 }}
          type={status === 'degraded' ? 'warning' : 'error'}
          showIcon
          message={`系统状态：${STATUS_LABEL[status] ?? status}`}
          description={
            status === 'not_ready'
              ? '系统尚未就绪，部分依赖不可用，请检查各检查项详情。'
              : '部分依赖处于降级状态，可能影响部分功能。'
          }
        />
      )}
      {status === 'ok' && (
        <Alert
          style={{ marginBottom: 16 }}
          type="success"
          showIcon
          message="系统状态：正常"
          description="所有检查项均正常。"
        />
      )}

      {/* 系统概要信息 */}
      <OceanPanel variant="strong" padding={0} style={{ marginBottom: 16 }}>
        <div style={{ padding: 16 }}>
          <Descriptions title="系统概要" bordered column={2} size="small">
            <Descriptions.Item label="整体状态">
              <StatusMark
                semantic={STATUS_SEMANTIC[status] ?? 'neutral'}
                label={STATUS_LABEL[status] ?? status}
              />
            </Descriptions.Item>
            <Descriptions.Item label="迁移版本">
              {health?.migration_version
                ? <span style={{ fontFamily: 'var(--ocean-font-mono)' }}>{health.migration_version}</span>
                : <Text type="secondary">未知</Text>}
            </Descriptions.Item>
            <Descriptions.Item label="Worker 心跳">
              {health?.worker_heartbeat
                ? new Date(health.worker_heartbeat).toLocaleString()
                : <Text type="secondary">无心跳</Text>}
            </Descriptions.Item>
            <Descriptions.Item label="Outbox 积压数">
              {health?.outbox_backlog !== undefined ? (
                <span
                  className="ocean-tabular-nums"
                  style={{
                    color: health.outbox_backlog > 100 ? 'var(--ocean-status-danger)' : 'inherit',
                    fontWeight: health.outbox_backlog > 100 ? 'bold' : 'normal',
                  }}
                >
                  {health.outbox_backlog}
                </span>
              ) : (
                <Text type="secondary">未知</Text>
              )}
            </Descriptions.Item>
          </Descriptions>
        </div>
      </OceanPanel>

      {/* 检查项详情 */}
      <OceanPanel variant="strong" padding={0}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--ocean-border-subtle)' }}>
          <Text strong style={{ color: 'var(--ocean-text-primary)' }}>检查项详情</Text>
        </div>
        <Table<HealthCheck>
          columns={columns}
          dataSource={checks}
          rowKey="name"
          pagination={false}
          size="middle"
        />
      </OceanPanel>
    </div>
  );
}
