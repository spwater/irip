import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

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
import { successfulLoginApi } from '@/test/mockApi';

describe('LoginPage', () => {
  it('logs in and returns to the requested route', async () => {
    renderApp({ initialUrl: '/facts', api: successfulLoginApi });

    // 使用 findByLabelText 等待异步认证初始化完成后 LoginPage 渲染
    const emailInput = await screen.findByLabelText('邮箱');
    await userEvent.type(emailInput, 'researcher@irip.local');

    const passwordInput = await screen.findByLabelText('密码');
    await userEvent.type(passwordInput, 'Correct-Horse-2026!');

    // Ant Design 会在两个中文字符之间自动插入空格（"登 录"），
    // 使用正则匹配以兼容此行为
    const loginButton = await screen.findByRole('button', { name: /登\s*录/ });
    await userEvent.click(loginButton);

    expect(await screen.findByRole('heading', { name: '实验事实' })).toBeVisible();
  });
});
