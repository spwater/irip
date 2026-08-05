/**
 * FlowDetail 局部类型和常量。
 *
 * 从 FlowDetail.tsx 提取，供主组件和子组件共用。
 */

import type { FlowSummary } from '@/api/equipment-flows';
import type { useAuthStore } from '@/features/auth/AuthProvider';

/**
 * H-16: 批量执行单项结果
 * - succeeded: 执行成功（唯一计为成功的状态）
 * - failed: 执行失败
 * - cancelled: 被取消
 * - timed_out: 轮询耗尽，未在超时内到达终态
 */
export interface BatchItemResult {
  fileName: string;
  status: 'succeeded' | 'failed' | 'cancelled' | 'timed_out';
  error?: string;
  runId?: string;
}

/** H-16: 批量轮询单项的最大尝试次数（120 * 2s = 240s = 4min） */
export const BATCH_POLL_MAX_ATTEMPTS = 120;

/** H-16: 批量轮询间隔（毫秒） */
export const BATCH_POLL_INTERVAL = 500;

/** H-16: 流程运行终态 */
export const FLOW_RUN_TERMINAL_STATUSES = ['succeeded', 'failed', 'cancelled'];

/** 当前用户类型（从 useAuthStore 推导） */
export type CurrentUser = ReturnType<typeof useAuthStore.getState>['user'];

/** canManage 函数依赖的用户类型 */
export type CanManageUser = NonNullable<CurrentUser>;

/** canManage 函数签名 */
export type CanManageFn = (flow: FlowSummary | undefined | null) => boolean;
