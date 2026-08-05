/**
 * 权限判断纯函数。
 *
 * 从 FlowDetail.tsx 提取。
 * 管理权限检查：所有者 + 上级向下 + 负责人管本部门。
 */

import type { FlowSummary } from '@/api/equipment-flows';
import type { CanManageUser } from '../types';

/**
 * 检查当前用户是否有权管理指定流程。
 *
 * 权限规则：
 * 1. 平台管理员不受限；
 * 2. 数据所有者可管理；
 * 3. 实验室负责人可管本部门成员的数据；
 * 4. 非同部门 → 需要后端判断是否是上级，前端保守返回 false。
 *
 * @param flow - 流程摘要（可能为 null）
 * @param currentUser - 当前登录用户
 * @returns 是否有管理权限
 */
export function canManage(
  flow: FlowSummary | undefined | null,
  currentUser: CanManageUser | null | undefined,
): boolean {
  if (!flow || !currentUser) return false;
  // 平台管理员不受限
  if (currentUser.roles.includes('platform_administrator')) return true;
  // 数据所有者可管理
  if (flow.owner_user_id && currentUser.id === flow.owner_user_id) return true;
  // 实验室负责人可管本部门成员的数据
  if (currentUser.roles.includes('lab_director') && flow.department_id === currentUser.departmentId) {
    return true;
  }
  // 非同部门 → 需要后端判断是否是上级，前端保守返回 false
  return false;
}
