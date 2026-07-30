import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// vi.mock 必须在 import 之前（vitest 会自动提升）
vi.mock('@/api/client', async () => {
  const { mockApiState } = await import('@/test/mockApi');
  return {
    apiLogin: (...args: unknown[]) =>
      mockApiState.current?.login?.(...(args as [string, string])) ??
      Promise.reject(new Error('apiLogin not mocked')),
    apiRefresh: () =>
      mockApiState.current?.refresh?.() ?? Promise.resolve(null),
    apiGetMe: () =>
      mockApiState.current?.getMe?.() ?? Promise.reject(new Error('apiGetMe not mocked')),
    apiLogout: () =>
      mockApiState.current?.logout?.() ?? Promise.resolve(undefined),
    apiGetJob: (id: string) =>
      mockApiState.current?.getJob?.(id) ??
      Promise.reject(new Error('apiGetJob not mocked')),
    setAccessToken: () => {},
    getAccessToken: () => null,
  };
});

import { runningJobApi, runningJob, setMockApi } from '@/test/mockApi';
import { useJobStore, setJobStoreScope } from '@/features/jobs/useJobStore';
import { JobDrawer } from '@/features/jobs/JobDrawer';

describe('JobDrawer', () => {
  it('restores an unfinished job after reload', async () => {
    // 注入 mock API
    setMockApi(runningJobApi);
    setJobStoreScope('test-org', 'test-user');

    // 预置 localStorage 中的 job ID
    localStorage.setItem('irip:test-org:test-user:jobs', JSON.stringify([runningJob.id]));

    // 重置 store
    useJobStore.getState().reset();
    setJobStoreScope('test-org', 'test-user');
    localStorage.setItem('irip:test-org:test-user:jobs', JSON.stringify([runningJob.id]));

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <JobDrawer />
      </QueryClientProvider>,
    );

    // loadJobs 异步加载后，有 active job 会自动打开 drawer
    await waitFor(() => {
      expect(screen.getByText('正在解析实验文件')).toBeVisible();
    }, { timeout: 5000 });
  });
});
