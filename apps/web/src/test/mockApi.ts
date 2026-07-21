import type { CurrentUser, JobSummary } from '@/api/client';

/**
 * 模拟 API 处理器接口
 */
export type MockApiHandlers = {
  login?: (email: string, password: string) => Promise<{ access_token: string; expires_in: number }>;
  refresh?: () => Promise<{ access_token: string; expires_in: number } | null>;
  getMe?: () => Promise<CurrentUser>;
  logout?: () => Promise<void>;
  getJob?: (id: string) => Promise<JobSummary>;
};

/**
 * 共享可变状态 — 测试通过 setMockApi 注入模拟处理器
 * vi.mock 工厂中通过 await import('@/test/mockApi') 获取此对象的引用
 */
export const mockApiState: { current: MockApiHandlers | null } = {
  current: null,
};

export function setMockApi(api: MockApiHandlers): void {
  mockApiState.current = api;
}

/**
 * 模拟 API：成功登录场景
 */
export const successfulLoginApi: MockApiHandlers = {
  login: async (_email: string, _password: string) => ({
    access_token: 'mock-access-token',
    expires_in: 900,
  }),
  getMe: async () => ({
    id: 'u-researcher-001',
    displayName: '研究员',
    roles: ['researcher'],
    permissions: ['facts:read', 'facts:write'],
  }),
  refresh: async () => null, // 未认证时 refresh 返回 null
  logout: async () => {},
};

/**
 * 模拟 API：运行中作业场景（已认证，有 running job）
 */
export const runningJobApi: MockApiHandlers = {
  refresh: async () => ({
    access_token: 'mock-access-token',
    expires_in: 900,
  }),
  getMe: async () => ({
    id: 'u-researcher-001',
    displayName: '研究员',
    roles: ['researcher'],
    permissions: ['facts:read', 'facts:write'],
  }),
  getJob: async (id: string) => ({
    id,
    kind: 'facts.parse_excel',
    status: 'running' as const,
    stage: '正在解析实验文件',
    progress: 42,
    retryable: true,
  }),
  logout: async () => {},
};

/**
 * 模拟数据：运行中的作业
 */
export const runningJob: JobSummary = {
  id: 'job-001',
  kind: 'facts.parse_excel',
  status: 'running',
  stage: '正在解析实验文件',
  progress: 42,
  retryable: true,
};
