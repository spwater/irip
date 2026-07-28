/**
 * 作业状态展示映射 — 供 JobsPage / JobDrawer / JobDetail 共享
 *
 * TypeScript 强制 Record<JobStatus, ...> 覆盖所有联合成员，
 * 缺少任何状态键将导致编译错误。
 */
import type { JobStatus } from '@/api/client';
import type { StatusTone } from '@/theme/tokens';

export const JOB_STATUS_VIEW: Record<JobStatus, { label: string; tone: StatusTone }> = {
  accepted: { label: '已接受', tone: 'neutral' },
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '运行中', tone: 'info' },
  retry_wait: { label: '等待重试', tone: 'warning' },
  succeeded: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  cancel_requested: { label: '取消中', tone: 'warning' },
  cancelled: { label: '已取消', tone: 'neutral' },
};

/** 终态：不需要继续轮询 */
export const TERMINAL_STATUSES: string[] = ['succeeded', 'failed', 'cancelled'];

/** 可取消状态集合 */
export const CANCELLABLE_STATUSES: string[] = [
  'accepted',
  'queued',
  'running',
  'retry_wait',
];

/**
 * 将后端 status 字符串安全转为 JobStatus，用于索引 JOB_STATUS_VIEW。
 * 未知状态回退到 neutral tone + 原始文本。
 */
export function jobStatusView(status: string): { label: string; tone: StatusTone } {
  if (status in JOB_STATUS_VIEW) {
    return JOB_STATUS_VIEW[status as JobStatus];
  }
  return { label: status, tone: 'neutral' as const };
}
