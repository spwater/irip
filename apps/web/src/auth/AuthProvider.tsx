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

/**
 * 认证状态管理（zustand store）
 * - access token 仅存于内存（模块级变量，通过 api/client.ts 的 setAccessToken 管理）
 * - refresh token 通过 HttpOnly cookie 自动携带
 * - /me 在 reload 时恢复用户信息
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
        set({ initialized: true });
        return false;
      }
      setAccessToken(result.access_token);
      const user = await apiGetMe();
      set({ user, initialized: true, error: null });
      return true;
    } catch {
      set({ initialized: true });
      return false;
    }
  },

  logout: async (): Promise<void> => {
    try {
      await apiLogout();
    } finally {
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
