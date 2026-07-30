import React from 'react';
import { render } from '@testing-library/react';
import { RouterProvider } from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createAppRouter } from '@/app/router';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { useJobStore, setJobStoreScope } from '@/features/jobs/useJobStore';
import { registerQueryClient } from '@/features/auth/sessionState';
import { setMockApi, type MockApiHandlers } from '@/test/mockApi';
import type { JobSummary } from '@/api/client';

/**
 * 测试辅助函数：渲染完整应用
 *
 * @param options.initialUrl - 初始 URL（默认 /workbench）
 * @param options.api - 模拟 API 处理器
 * @param options.storedJobs - 预置 localStorage 的作业列表（只存 ID）
 */
export function renderApp(options: {
  initialUrl?: string;
  api?: MockApiHandlers;
  storedJobs?: JobSummary[];
}): void {
  // 注入模拟 API
  setMockApi(options.api ?? {});

  // 重置 auth store
  useAuthStore.getState().reset();

  // 重置 job store
  useJobStore.getState().reset();

  // H-15: 设置测试用 job store scope（tenant+user）
  setJobStoreScope('test-org', 'test-user');

  // 预置 localStorage 中的 job ID 列表（使用 H-15 的 scoped key）
  if (options.storedJobs && options.storedJobs.length > 0) {
    const jobIds = options.storedJobs.map((j) => j.id);
    localStorage.setItem('irip:test-org:test-user:jobs', JSON.stringify(jobIds));
  }

  // 设置初始 URL
  const url = options.initialUrl ?? '/workbench';
  window.history.pushState({}, '', url);

  // 创建新路由器并渲染（包裹 QueryClientProvider 供 TanStack Query 使用）
  const router = createAppRouter();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // H-15: 注册 QueryClient 供 clearSessionState 使用
  registerQueryClient(queryClient);
  render(
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(RouterProvider, { router }),
    ),
  );
}
