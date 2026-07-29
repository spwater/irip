import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';

/**
 * IRIP API 客户端类型定义
 */

export type CurrentUser = {
  id: string;
  displayName: string;
  roles: string[];
  permissions: string[];
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
 * 响应拦截器：401 时自动尝试 refresh 并重试一次
 * 重试仍 401 → 跳转登录页
 */
let isRefreshing = false;

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retried?: boolean } | undefined;
    if (error.response?.status === 401 && originalRequest && !originalRequest._retried) {
      if (isRefreshing) {
        return Promise.reject(error);
      }
      isRefreshing = true;
      try {
        const res = await axios.post<LoginResponse>(
          `${baseURL}/auth/refresh`,
          {},
          { withCredentials: true },
        );
        accessToken = res.data.access_token;
        originalRequest._retried = true;
        originalRequest.headers.set('Authorization', `Bearer ${accessToken}`);
        return http.request(originalRequest);
      } catch {
        // 不用 window.location.href 硬跳转，否则会触发整页刷新→init()→401→刷新 死循环
        // 只清除 token，让 AuthProvider/AppShell 通过 router navigate 处理跳转
        accessToken = null;
        return Promise.reject(error);
      } finally {
        isRefreshing = false;
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
    // 用裸 axios 绕过响应拦截器，避免 refresh 失败时触发 window.location.href 硬跳转导致无限刷新
    const res = await axios.post<LoginResponse>(
      `${baseURL}/auth/refresh`,
      {},
      { withCredentials: true },
    );
    return res.data;
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