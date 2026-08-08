import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { PageHeaderProvider, usePageHeader } from '@/app/PageHeaderContext';
import { PlatformPage } from './PlatformPage';
import { useAuthStore } from '@/features/auth/AuthProvider';

// Mock child components
vi.mock('@/features/assistant/AssistantPage', () => ({
  AssistantPage: () => <div data-testid="assistant-page">AssistantPage</div>,
}));
vi.mock('@/features/ai-tools/AIToolsPage', () => ({
  AIToolsPage: () => <div data-testid="ai-tools-page">AIToolsPage</div>,
}));
vi.mock('@/features/components/ComponentsPage', () => ({
  ComponentsPage: () => <div data-testid="components-page">ComponentsPage</div>,
}));
vi.mock('@/features/platform/PersonalSettings', () => ({
  PersonalSettings: () => <div data-testid="personal-settings-page">PersonalSettings</div>,
}));

// Mock router
const mockNavigate = vi.fn();
let mockSearch: Record<string, unknown> = {};
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useSearch: () => mockSearch,
}));

function HeaderProbe(): JSX.Element {
  const { header } = usePageHeader();
  return (
    <div>
      <span data-testid="header-title">{header.title ?? ''}</span>
      <span data-testid="active-tab">{header.activeTab ?? ''}</span>
      <div data-testid="header-tabs">
        {(header.tabs ?? []).map((t) => (
          <button key={t.key} data-testid={`tab-${t.key}`} onClick={() => header.onTabChange?.(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function renderPage(): void {
  render(
    <PageHeaderProvider>
      <AntApp>
        <HeaderProbe />
        <PlatformPage />
      </AntApp>
    </PageHeaderProvider>,
  );
}

describe('PlatformPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSearch = {};
    useAuthStore.getState().reset();
  });

  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it('renders title 平台应用 and defaults to assistant tab', () => {
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    expect(screen.getByTestId('header-title')).toHaveTextContent('平台应用');
    expect(screen.getByTestId('active-tab')).toHaveTextContent('assistant');
  });

  it('renders AssistantPage by default', () => {
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    expect(screen.getByTestId('assistant-page')).toBeInTheDocument();
  });

  it('shows ai-tools tab only for platform_administrator', () => {
    useAuthStore.setState({
      user: { id: 'u-admin', displayName: '管理员', roles: ['platform_administrator'], permissions: [] },
    });
    renderPage();
    expect(screen.getByTestId('tab-ai-tools')).toBeInTheDocument();
  });

  it('hides ai-tools tab for non-admin users', () => {
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    expect(screen.queryByTestId('tab-ai-tools')).not.toBeInTheDocument();
  });

  it('switches to components tab', async () => {
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    await userEvent.click(screen.getByTestId('tab-components'));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/platform',
      search: { tab: 'components' },
      replace: true,
    });
  });

  it('switches to personal-settings tab', async () => {
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    await userEvent.click(screen.getByTestId('tab-personal-settings'));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/platform',
      search: { tab: 'personal-settings' },
      replace: true,
    });
  });

  it('renders ComponentsPage when tab=components', () => {
    mockSearch = { tab: 'components' };
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    expect(screen.getByTestId('components-page')).toBeInTheDocument();
  });

  it('renders PersonalSettings when tab=personal-settings', () => {
    mockSearch = { tab: 'personal-settings' };
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    expect(screen.getByTestId('personal-settings-page')).toBeInTheDocument();
  });
});
