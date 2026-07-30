import { useEffect, type ReactNode } from 'react';
import { Spin } from 'antd';
import { create } from 'zustand';
import {
  apiLogin,
  apiRefresh,
  apiGetMe,
  apiLogout,
  setAccessToken,
  type CurrentUser,
} from '@/api/client';
import { clearSessionState, registerCleanupCallback } from '@/auth/sessionState';
import { setJobStoreScope } from '@/jobs/useJobStore';

/**
 * 认证状态管理（zustand store）
 * - access token 仅存于内存（模块级变量，通过 api/client.ts 的 setAccessToken 管理）
 * - refresh token 通过 HttpOnly cookie 自动携带
 * - /me 在 reload 时恢复用户信息
 *
 * H-15: 登出/refresh 失败/账号切换时调用 clearSessionState() 原子清理
 * M-04: refresh 失败时原子清会话跳登录
 */

interface AuthState {
  /** 当前登录用户，null 表示未认证 */
  user: CurrentUser | null;
  /** 是否正在加载（登录中、刷新中） */
  loading: boolean;
  /** 是否已完成初始化（首次 refresh 尝试完毕） */
  initialized: boolean;
  /** 错误信息 */
  error: string | null;
  /** 登录 */
  login: (email: string, password: string) => Promise<boolean>;
  /** 刷新 token（通过 HttpOnly cookie） */
  refresh: () => Promise<boolean>;
  /** 登出 */
  logout: () => Promise<void>;
  /** 初始化：页面加载时尝试恢复会话 */
  init: () => Promise<void>;
  /** 重置状态（测试用） */
  reset: () => void;
}

/**
 * 根据用户信息推导租户标识。
 * 后端 /me 可能尚未返回 organization_id，此时使用 'unknown' 作为 fallback。
 * 用户 id 始终可用，确保跨用户隔离。
 */
function deriveTenant(user: CurrentUser): string {
  return user.organizationId ?? 'unknown';
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  loading: false,
  initialized: false,
  error: null,

  login: async (email: string, password: string): Promise<boolean> => {
    set({ loading: true, error: null });
    try {
      const { access_token } = await apiLogin(email, password);
      setAccessToken(access_token);
      const user = await apiGetMe();

      // H-15: 账号切换场景 — 如果已有旧用户，先原子清理旧会话
      const prevUser = get().user;
      if (prevUser) {
        clearSessionState({
          tenant: deriveTenant(prevUser),
          user: prevUser.id,
        });
      }

      // 设置作业 store 的存储范围（含 tenant+user）
      setJobStoreScope(deriveTenant(user), user.id);

      set({ user, loading: false, initialized: true, error: null });
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : '登录失败';
      set({ loading: false, error: message });
      return false;
    }
  },

  refresh: async (): Promise<boolean> => {
    try {
      const result = await apiRefresh();
      if (!result) {
        // M-04: refresh 失败，原子清会话
        clearSessionState();
        set({ user: null, initialized: true });
        return false;
      }
      setAccessToken(result.access_token);
      const user = await apiGetMe();

      // H-15: 设置作业 store 的存储范围
      setJobStoreScope(deriveTenant(user), user.id);

      set({ user, initialized: true, error: null });
      return true;
    } catch {
      // M-04: refresh 异常时也原子清会话
      clearSessionState();
      set({ user: null, initialized: true });
      return false;
    }
  },

  logout: async (): Promise<void> => {
    try {
      await apiLogout();
    } finally {
      // H-15: 登出时原子清理 Query/Zustand/localStorage
      clearSessionState();
      setAccessToken(null);
      set({ user: null, error: null });
    }
  },

  init: async (): Promise<void> => {
    if (get().initialized) return;
    await get().refresh();
  },

  reset: (): void => {
    setAccessToken(null);
    set({ user: null, loading: false, initialized: false, error: null });
  },
}));

// H-15 + M-04: 注册清理回调，供 client.ts 拦截器 refresh 失败时调用
// clearSessionState() 会执行此回调，将 auth store 的 user 置空，
// 触发 AppShell 的 useEffect 跳转登录页
registerCleanupCallback(() => {
  setAccessToken(null);
  useAuthStore.setState({ user: null, error: null });
});

/**
 * AuthProvider 组件
 * - 在挂载时调用 init() 尝试恢复会话
 * - 初始化期间显示加载指示器
 * - 初始化完成后渲染子组件
 */
export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const initialized = useAuthStore((s) => s.initialized);
  const init = useAuthStore((s) => s.init);

  useEffect(() => {
    void init();
  }, [init]);

  if (!initialized) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', height: '100vh', gap: 16 }}>
        <Spin size="large" />
        <span style={{ color: 'var(--ocean-text-muted)' }}>正在加载…</span>
      </div>
    );
  }

  return <>{children}</>;
}
