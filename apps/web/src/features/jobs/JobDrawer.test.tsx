import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';

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

import { renderApp } from '@/test/setup';
import { runningJobApi, runningJob } from '@/test/mockApi';

describe('JobDrawer', () => {
  it('restores an unfinished job after reload', async () => {
    renderApp({ storedJobs: [runningJob], api: runningJobApi });

    expect(await screen.findByText('正在解析实验文件')).toBeVisible();
  });
});
