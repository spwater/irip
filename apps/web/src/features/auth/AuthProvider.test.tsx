import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { act } from '@testing-library/react';

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
    setAccessToken: vi.fn(),
    getAccessToken: () => null,
  };
});

vi.mock('@/features/jobs/useJobStore', () => ({
  setJobStoreScope: vi.fn(),
  useJobStore: () => ({ reset: vi.fn() }),
}));

import { useAuthStore, AuthProvider } from './AuthProvider';
import { setMockApi, type MockApiHandlers } from '@/test/mockApi';

function renderWithProvider(children: React.ReactNode) {
  return render(<AuthProvider>{children}</AuthProvider>);
}

describe('AuthProvider', () => {
  beforeEach(() => {
    useAuthStore.getState().reset();
    setMockApi({});
  });

  it('shows loading spinner before initialization', () => {
    // render while not initialized — init() will run but we check initial state
    useAuthStore.getState().reset(); // ensure initialized=false
    renderWithProvider(<div>child-content</div>);
    expect(screen.getByText('正在加载…')).toBeInTheDocument();
    expect(screen.queryByText('child-content')).not.toBeInTheDocument();
  });

  it('renders children after successful init', async () => {
    const mockApi: MockApiHandlers = {
      refresh: async () => ({ access_token: 'tok', expires_in: 900 }),
      getMe: async () => ({
        id: 'u1',
        displayName: 'Test User',
        roles: ['researcher'],
        permissions: [],
      }),
    };
    setMockApi(mockApi);

    renderWithProvider(<div>child-content</div>);

    await waitFor(() => {
      expect(screen.getByText('child-content')).toBeInTheDocument();
    });
  });

  it('login succeeds and sets user', async () => {
    const mockApi: MockApiHandlers = {
      login: async () => ({ access_token: 'tok', expires_in: 900 }),
      getMe: async () => ({
        id: 'u1',
        displayName: 'Tester',
        roles: ['researcher'],
        permissions: [],
      }),
    };
    setMockApi(mockApi);

    let result: boolean | undefined;
    await act(async () => {
      result = await useAuthStore.getState().login('test@irip.local', 'pw');
    });

    expect(result).toBe(true);
    expect(useAuthStore.getState().user).not.toBeNull();
    expect(useAuthStore.getState().user?.displayName).toBe('Tester');
    expect(useAuthStore.getState().error).toBeNull();
  });

  it('login failure sets error and returns false', async () => {
    setMockApi({
      login: async () => {
        throw new Error('invalid credentials');
      },
    });

    let result: boolean | undefined;
    await act(async () => {
      result = await useAuthStore.getState().login('bad@irip.local', 'wrong');
    });

    expect(result).toBe(false);
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().error).toBe('invalid credentials');
  });

  it('logout clears user', async () => {
    // First set up a logged-in state
    setMockApi({
      refresh: async () => ({ access_token: 'tok', expires_in: 900 }),
      getMe: async () => ({
        id: 'u1',
        displayName: 'Tester',
        roles: ['researcher'],
        permissions: [],
      }),
      logout: async () => {},
    });

    await act(async () => {
      await useAuthStore.getState().refresh();
    });
    expect(useAuthStore.getState().user).not.toBeNull();

    await act(async () => {
      await useAuthStore.getState().logout();
    });

    expect(useAuthStore.getState().user).toBeNull();
  });

  it('refresh failure clears user and sets initialized', async () => {
    setMockApi({
      refresh: async () => null,
    });

    await act(async () => {
      await useAuthStore.getState().refresh();
    });

    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().initialized).toBe(true);
  });

  it('init does not re-run if already initialized', async () => {
    setMockApi({
      refresh: async () => ({ access_token: 'tok', expires_in: 900 }),
      getMe: async () => ({
        id: 'u1',
        displayName: 'Tester',
        roles: ['researcher'],
        permissions: [],
      }),
    });

    await act(async () => {
      await useAuthStore.getState().init();
    });
    expect(useAuthStore.getState().initialized).toBe(true);

    // Second init should be a no-op even if refresh changes
    const refreshSpy = vi.spyOn(useAuthStore.getState(), 'refresh');
    await act(async () => {
      await useAuthStore.getState().init();
    });
    expect(refreshSpy).not.toHaveBeenCalled();
  });
});
