import { useEffect } from 'react';
import { Drawer, Empty, Progress, Spin, Tag, Typography } from 'antd';
import { useJobStore, ACTIVE_STATUSES } from './useJobStore';
import type { JobStatus } from '@/api/client';

const { Text } = Typography;

/** 状态 → 颜色映射 */
const STATUS_COLOR: Record<JobStatus, string> = {
  accepted: 'default',
  queued: 'blue',
  running: 'processing',
  retry_wait: 'orange',
  succeeded: 'success',
  failed: 'error',
  cancel_requested: 'warning',
  cancelled: 'default',
};

/** 状态 → 中文标签映射 */
const STATUS_LABEL: Record<JobStatus, string> = {
  accepted: '已接受',
  queued: '排队中',
  running: '运行中',
  retry_wait: '等待重试',
  succeeded: '已完成',
  failed: '已失败',
  cancel_requested: '取消请求中',
  cancelled: '已取消',
};

/**
 * 全局作业进度抽屉
 * - 持久化 job ID 到 localStorage（只存 ID，不存状态）
 * - 从 API 刷新权威状态
 * - 显示作业进度、状态、重试信息
 */
export function JobDrawer(): JSX.Element {
  const jobs = useJobStore((s) => s.jobs);
  const loading = useJobStore((s) => s.loading);
  const drawerOpen = useJobStore((s) => s.drawerOpen);
  const setDrawerOpen = useJobStore((s) => s.setDrawerOpen);
  const loadJobs = useJobStore((s) => s.loadJobs);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  const activeCount = jobs.filter((j) => ACTIVE_STATUSES.includes(j.status)).length;

  // 有活跃作业时每 5 秒轮询刷新状态
  useEffect(() => {
    if (activeCount === 0) return;
    const interval = setInterval(() => {
      void loadJobs();
    }, 5000);
    return () => clearInterval(interval);
  }, [activeCount, loadJobs]);

  return (
    <Drawer
      title="作业进度"
      open={drawerOpen}
      onClose={() => setDrawerOpen(false)}
      width={420}
      mask={false}
      placement="right"
    >
      {loading && jobs.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin tip="加载中…" />
        </div>
      ) : jobs.length === 0 ? (
        <Empty description="暂无作业" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {jobs.map((job) => (
            <div
              key={job.id}
              style={{
                padding: 16,
                border: '1px solid var(--ocean-border-subtle)',
                borderRadius: 6,
                background: 'var(--ocean-surface-default)',
              }}
            >
              <div style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between' }}>
                <Text strong>{job.stage}</Text>
                <Tag color={STATUS_COLOR[job.status]}>{STATUS_LABEL[job.status]}</Tag>
              </div>
              <Progress percent={job.progress} size="small" />
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--ocean-text-muted)' }}>
                类型: {job.kind}
                {job.retryable && <Tag color="orange" style={{ marginLeft: 8 }}>可重试</Tag>}
              </div>
            </div>
          ))}
          {activeCount > 0 && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {activeCount} 个作业进行中…
            </Text>
          )}
        </div>
      )}
    </Drawer>
  );
}

/**
 * 作业进度按钮 — 渐变半透明胶囊，与 Header tab 统一风格
 */
export function JobDrawerButton(): JSX.Element {
  const setDrawerOpen = useJobStore((s) => s.setDrawerOpen);
  const jobs = useJobStore((s) => s.jobs);
  const activeCount = jobs.filter((j) => ACTIVE_STATUSES.includes(j.status)).length;

  return (
    <button
      type="button"
      onClick={() => setDrawerOpen(true)}
      style={{
        padding: '8px 22px',
        fontSize: 14,
        fontWeight: 500,
        color: 'var(--ocean-abyss-deep)',
        background: 'linear-gradient(120deg, rgba(23, 184, 206, 0.38) 0%, rgba(23, 184, 206, 0.18) 55%, rgba(23, 184, 206, 0.08) 100%)',
        border: '1px solid rgba(23, 184, 206, 0.55)',
        borderRadius: 999,
        cursor: 'pointer',
        transition: 'all 200ms var(--ocean-motion-easing)',
        lineHeight: 1.4,
        whiteSpace: 'nowrap',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.7), 0 4px 14px rgba(14, 91, 132, 0.18)',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = 'linear-gradient(120deg, rgba(23, 184, 206, 0.48) 0%, rgba(23, 184, 206, 0.28) 55%, rgba(23, 184, 206, 0.16) 100%)';
        e.currentTarget.style.borderColor = 'rgba(23, 184, 206, 0.7)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'linear-gradient(120deg, rgba(23, 184, 206, 0.38) 0%, rgba(23, 184, 206, 0.18) 55%, rgba(23, 184, 206, 0.08) 100%)';
        e.currentTarget.style.borderColor = 'rgba(23, 184, 206, 0.55)';
      }}
    >
      作业进度{activeCount > 0 ? ` (${activeCount})` : ''}
    </button>
  );
}
