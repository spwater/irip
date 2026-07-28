import {
  Button,
  Descriptions,
  Space,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import {
  apiGetSystemHealth,
  type HealthCheck,
  type SystemHealth,
} from '@/api/client';
import { useAuthStore } from '@/auth/AuthProvider';
import { StatusMark, FeedbackState } from '@/components/ui';
import type { StatusTone } from '@/theme/tokens';

const { Text } = Typography;

/** 整体状态 → StatusTone 映射 */
const STATUS_TONE: Record<string, StatusTone> = {
  ok: 'success',
  degraded: 'warning',
  not_ready: 'danger',
  error: 'danger',
};

/** 整体状态 → 中文标签映射 */
const STATUS_LABEL: Record<string, string> = {
  ok: '正常',
  degraded: '降级',
  not_ready: '未就绪',
  error: '异常',
};

/** 检查项状态 → StatusTone 映射 */
const CHECK_TONE: Record<string, StatusTone> = {
  ok: 'success',
  error: 'danger',
  degraded: 'warning',
  warning: 'warning',
};

/** 检查项状态 → 中文标签映射 */
const CHECK_LABEL: Record<string, string> = {
  ok: '正常',
  error: '异常',
  degraded: '降级',
  warning: '警告',
};

/**
 * 系统健康页面
 *
 * 功能：
 * - 整体状态（ready / degraded / not_ready）
 * - 各检查项状态（名称 / 状态 / 延迟 / 消息）— 使用 StatusMark 非颜色依赖
 * - 迁移版本
 * - Worker 心跳时间
 * - Outbox 积压数
 * - 保留 apiGetSystemHealth 的 503-body 提取行为
 */
export function SystemHealthPage(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const canView: boolean = user?.permissions?.includes('system:health') ?? true;

  // ---- 数据查询：系统健康 ----
  const { data, isLoading, isError, refetch, isFetching, error } = useQuery({
    queryKey: ['system-health'],
    queryFn: apiGetSystemHealth,
    refetchInterval: 30000, // 每 30 秒自动刷新
    enabled: canView,
  });

  // ---- 权限检查 ----
  if (!canView) {
    return (
      <div>
        <Text type="danger">您没有系统健康监控权限。</Text>
      </div>
    );
  }

  // ---- 加载中 ----
  if (isLoading) {
    return (
      <FeedbackState kind="loading" title="加载系统健康状态…" rows={4} />
    );
  }

  // ---- 查询错误（非 503-body 提取场景） ----
  if (isError && !data) {
    const errorDetail = error instanceof Error ? error.message : '系统健康状态获取失败';
    return (
      <FeedbackState
        kind="error"
        title="系统健康状态获取失败"
        description={errorDetail}
        onRetry={() => void refetch()}
      />
    );
  }

  const health: SystemHealth | undefined = data;
  const status: string = health?.status ?? 'not_ready';
  const checks: HealthCheck[] = health?.checks ?? [];
  const overallTone = STATUS_TONE[status] ?? 'danger';
  const overallLabel = STATUS_LABEL[status] ?? status;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Typography.Title level={5} style={{ margin: 0, paddingTop: '1em' }}>系统健康</Typography.Title>
        <Space>
          <Button onClick={() => refetch()} loading={isFetching}>
            刷新
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            每 30 秒自动刷新
          </Text>
        </Space>
      </div>

      {/* 整体状态标记 */}
      <div style={{ marginBottom: 16 }}>
        <StatusMark
          tone={overallTone}
          label={`系统状态：${overallLabel}`}
          detail={
            status === 'not_ready'
              ? '系统尚未就绪，部分依赖不可用，请检查各检查项详情。'
              : status === 'degraded'
                ? '部分依赖处于降级状态，可能影响部分功能。'
                : status === 'ok'
                  ? '所有检查项均正常。'
                  : undefined
          }
        />
      </div>

      {/* 系统概要信息 */}
      <div style={{ marginBottom: 16 }}>
        <Descriptions title="系统概要" bordered column={2} size="small">
          <Descriptions.Item label="整体状态">
            <StatusMark tone={overallTone} label={overallLabel} />
          </Descriptions.Item>
          <Descriptions.Item label="迁移版本">
            {health?.migration_version ?? <Text type="secondary">未知</Text>}
          </Descriptions.Item>
          <Descriptions.Item label="Worker 心跳">
            {health?.worker_heartbeat
              ? new Date(health.worker_heartbeat).toLocaleString()
              : <Text type="secondary">无心跳</Text>}
          </Descriptions.Item>
          <Descriptions.Item label="Outbox 积压数">
            {health?.outbox_backlog !== undefined ? (
              <span
                className="ocean-tabular-number"
                style={{
                  color: health.outbox_backlog > 100 ? '#A53D52' : 'inherit',
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

      {/* 检查项详情 — 使用 StatusMark 非颜色依赖标记 */}
      <section aria-label="系统健康状态">
        <Typography.Title level={5} style={{ marginBottom: 12 }}>检查项详情</Typography.Title>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {checks.length === 0 ? (
            <Text type="secondary">暂无检查项数据</Text>
          ) : (
            checks.map((check) => {
              const checkTone = CHECK_TONE[check.status] ?? 'neutral';
              const checkLabel = CHECK_LABEL[check.status] ?? check.status;
              const detailParts: string[] = [];
              if (check.latency_ms !== null && check.latency_ms !== undefined) {
                detailParts.push(`${check.latency_ms}ms`);
              }
              if (check.message) {
                detailParts.push(check.message);
              }
              return (
                <StatusMark
                  key={check.name}
                  tone={checkTone}
                  label={`${check.name} — ${checkLabel}`}
                  detail={detailParts.length > 0 ? detailParts.join(' · ') : undefined}
                />
              );
            })
          )}
        </Space>
      </section>
    </div>
  );
}
