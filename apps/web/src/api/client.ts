import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { clearSessionState } from '@/features/auth/sessionState';

/**
 * IRIP API 客户端类型定义
 */

export type CurrentUser = {
  id: string;
  displayName: string;
  roles: string[];
  permissions: string[];
  /** 组织/租户 ID，由后端 /me 返回；可能为空（后端未返回时） */
  departmentId?: string;
  /** irip-ai-collab: 用户头像 URL */
  avatarUrl?: string;
  /** root 部门成员，管理权限不受部门限制 */
  isRootMember?: boolean;
};

export type JobStatus =
  | 'accepted'
  | 'queued'
  | 'running'
  | 'retry_wait'
  | 'succeeded'
  | 'failed'
  | 'cancel_requested'
  | 'cancelled';

export type JobSummary = {
  id: string;
  kind: string;
  status: JobStatus;
  stage: string;
  progress: number;
  retryable: boolean;
};

export type LoginResponse = {
  access_token: string;
  expires_in: number;
};

/** 后端 /jobs/{id} 实际返回的原始结构 */
type JobApiResponse = {
  job_id: string;
  status: string;
  kind: string;
  stage?: string;
  progress?: number;
  retryable?: boolean;
};

/** 后端 /me 实际返回的原始结构 */
type MeApiResponse = {
  id: string;
  email: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  department_id?: string;
  /** irip-ai-collab: 头像 URL */
  avatar_url?: string;
  /** root 部门成员标记 */
  is_root_member?: boolean;
};

/**
 * Axios 实例
 * - baseURL 从环境变量 VITE_API_BASE_URL 读取，默认 /api/v1
 * - withCredentials: true — 携带 HttpOnly refresh cookie
 */
const baseURL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

export const http = axios.create({
  baseURL,
  withCredentials: true,
});

/** access token 仅存于内存，不持久化到 localStorage */
let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/**
 * 请求拦截器：自动添加 Authorization header
 */
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) {
    config.headers.set('Authorization', `Bearer ${accessToken}`);
  }
  return config;
});

/**
 * M-04: 统一 refresh coordinator（single-flight）
 *
 * N 个并行 401 只刷新一次：所有并发请求共享同一个 refresh Promise。
 * refresh 成功后所有等待请求自动重试；refresh 失败时原子清会话。
 *
 * 关键修复：apiRefresh() 和 refreshAccessToken() 必须共享同一个 single-flight 锁，
 * 否则页面刷新时 AuthProvider.init() 和响应拦截器各发一个 refresh 请求，
 * 第二个请求的 token 已被第一个旋转 → 后端判定重放攻击 → 撤销整个 family → 登出。
 */
let refreshPromise: Promise<LoginResponse> | null = null;

/**
 * 执行 single-flight token refresh（核心函数）。
 * - 首次调用发起实际的 /auth/refresh 请求
 * - 并发调用（apiRefresh / refreshAccessToken）复用同一个 Promise
 * - 成功返回 LoginResponse，失败时原子清会话并抛出
 */
function doRefresh(): Promise<LoginResponse> {
  if (refreshPromise) {
    return refreshPromise;
  }
  refreshPromise = axios
    .post<LoginResponse>(
      `${baseURL}/auth/refresh`,
      {},
      { withCredentials: true },
    )
    .then((res) => {
      accessToken = res.data.access_token;
      return res.data;
    })
    .catch((err) => {
      // refresh 失败：原子清会话（清 Query/Zustand/localStorage）
      clearSessionState();
      accessToken = null;
      throw err;
    })
    .finally(() => {
      refreshPromise = null;
    });
  return refreshPromise;
}

/**
 * 执行 single-flight token refresh，返回 access token 字符串。
 * 供响应拦截器使用。
 */
function refreshAccessToken(): Promise<string> {
  return doRefresh().then((data) => data.access_token);
}

/**
 * 响应拦截器：401 时通过 single-flight coordinator 刷新并重试一次
 * refresh 失败 → clearSessionState 原子清会话，由 AuthProvider/AppShell 跳登录
 */
http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;
    if (error.response?.status === 401 && originalRequest && !originalRequest._retried) {
      try {
        // single-flight：N 个并行 401 只刷新一次
        const newToken = await refreshAccessToken();
        originalRequest._retried = true;
        originalRequest.headers.set('Authorization', `Bearer ${newToken}`);
        return http.request(originalRequest);
      } catch {
        // refresh 失败已在 refreshAccessToken 中处理（clearSessionState）
        return Promise.reject(error);
      }
    }
    return Promise.reject(error);
  },
);

/**
 * API 函数封装
 */

export async function apiLogin(email: string, password: string): Promise<LoginResponse> {
  const res = await http.post<LoginResponse>('/auth/login', { email, password });
  return res.data;
}

export async function apiRefresh(): Promise<LoginResponse | null> {
  try {
    // 通过 doRefresh() 共享 single-flight 锁，避免与 refreshAccessToken() 竞态
    return await doRefresh();
  } catch {
    return null;
  }
}

export async function apiGetMe(): Promise<CurrentUser> {
  const res = await http.get<MeApiResponse>('/me');
  return {
    id: res.data.id,
    displayName: res.data.display_name,
    roles: res.data.roles ?? [],
    permissions: res.data.permissions ?? [],
    departmentId: res.data.department_id,
    avatarUrl: res.data.avatar_url,
    isRootMember: res.data.is_root_member ?? false,
  };
}

export async function apiLogout(): Promise<void> {
  await http.post('/auth/logout');
}

export async function apiGetJob(id: string): Promise<JobSummary> {
  const res = await http.get<JobApiResponse>(`/jobs/${id}`);
  return {
    id: res.data.job_id,
    kind: res.data.kind,
    status: res.data.status as JobStatus,
    stage: res.data.stage ?? '',
    progress: res.data.progress ?? 0,
    retryable: res.data.retryable ?? false,
  };
}

export async function apiCreateJob(
  kind: string,
  payload: Record<string, unknown>,
  idempotencyKey: string,
): Promise<{ job_id: string }> {
  const res = await http.post<{ job_id: string }>('/jobs', {
    kind,
    payload,
    idempotency_key: idempotencyKey,
  });
  return res.data;
}

export async function apiCancelJob(id: string): Promise<{ job_id: string; status: string; kind: string }> {
  const res = await http.post<{ job_id: string; status: string; kind: string }>(`/jobs/${id}/cancel`);
  return res.data;
}
