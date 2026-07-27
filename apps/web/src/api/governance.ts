/**
 * 治理与审计 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的治理（用户/角色）和审计事件相关类型和函数。
 * 通过 re-export 保持与 client.ts 的兼容性。
 */

export {
  type UserListItem,
  type UserListResponse,
  type AuditEventItem,
  type AuditEventListResponse,
  type AuditExportResponse,
  apiListUsers,
  apiAssignRoles,
  apiRemoveRole,
  apiUpdateUserStatus,
  apiListAuditEvents,
  apiCreateAuditExport,
} from './client';
