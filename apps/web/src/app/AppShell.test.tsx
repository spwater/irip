import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';

// vi.mock 必须在 import 之前（vitest 会自动提升）
// 工厂函数通过 await import 获取共享 mockApiState 引用
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
import { runningJobApi } from '@/test/mockApi';

describe('AppShell', () => {
  it('keeps all five primary destinations in the Data Ocean shell', async () => {
    renderApp({ initialUrl: '/workbench', api: runningJobApi });
    expect(await screen.findByRole('navigation', { name: '主导航' })).toBeVisible();
    for (const label of ['研发看板', '实验室建设', '实验室运营', '平台应用', '平台治理']) {
      expect(screen.getByRole('menuitem', { name: label })).toBeVisible();
    }
    expect(screen.getByTestId('ocean-app-content')).toBeVisible();
  });
});
