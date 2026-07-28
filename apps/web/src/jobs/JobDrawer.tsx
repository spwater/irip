import { useEffect } from 'react';
import { Button, Empty, Progress, Spin, Tag, Typography } from 'antd';
import { useJobStore, ACTIVE_STATUSES } from './useJobStore';
import { FocusDrawer, StatusMark } from '@/components/ui';
import { jobStatusView } from './jobPresentation';

const { Text } = Typography;

/**
 * 全局作业进度抽屉
 * - 持久化 job ID 到 localStorage（只存 ID，不存状态）
 * - 从 API 刷新权威状态
 * - 显示作业进度、状态、重试信息
 * - 使用共享 JOB_STATUS_VIEW 状态映射
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

  return (
    <FocusDrawer
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
          {jobs.map((job) => {
            const view = jobStatusView(job.status);
            return (
              <div
                key={job.id}
                style={{
                  padding: 16,
                  border: '1px solid rgba(24, 102, 133, 0.16)',
                  borderRadius: 6,
                  background: 'rgba(240, 250, 251, 0.72)',
                }}
              >
                <div style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Text strong>{job.stage}</Text>
                  <StatusMark tone={view.tone} label={view.label} />
                </div>
                <Progress percent={job.progress} size="small" />
                <div style={{ marginTop: 8, fontSize: 12, color: '#6F8D9C' }}>
                  类型: <span className="ocean-tech">{job.kind}</span>
                  {job.retryable && <Tag color="orange" style={{ marginLeft: 8 }}>可重试</Tag>}
                </div>
              </div>
            );
          })}
          {activeCount > 0 && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {activeCount} 个作业进行中…
            </Text>
          )}
        </div>
      )}
    </FocusDrawer>
  );
}

/**
 * 作业进度按钮 — 可放在 Header 中打开抽屉
 */
export function JobDrawerButton(): JSX.Element {
  const setDrawerOpen = useJobStore((s) => s.setDrawerOpen);
  const jobs = useJobStore((s) => s.jobs);
  const activeCount = jobs.filter((j) => ACTIVE_STATUSES.includes(j.status)).length;

  return (
    <Button onClick={() => setDrawerOpen(true)}>
      作业进度{activeCount > 0 ? ` (${activeCount})` : ''}
    </Button>
  );
}
