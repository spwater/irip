/**
 * 认证 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的认证相关类型和函数。
 * 后续可逐步将实现迁移到此模块，当前通过 re-export 保持兼容。
 */

export {
  type CurrentUser,
  type LoginResponse,
  apiLogin,
  apiRefresh,
  apiGetMe,
  apiLogout,
  setAccessToken,
  getAccessToken,
} from './client';
