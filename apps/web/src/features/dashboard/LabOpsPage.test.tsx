import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { PageHeaderProvider, usePageHeader } from '@/app/PageHeaderContext';
import { LabOpsPage } from './LabOpsPage';
import { useAuthStore } from '@/features/auth/AuthProvider';

// Mock child components
vi.mock('@/features/experiment-project/ProjectList', () => ({
  ProjectList: () => <div data-testid="project-list">ProjectList</div>,
}));
vi.mock('@/features/experiment-project/ProjectDetail', () => ({
  ProjectDetail: () => <div data-testid="project-detail">ProjectDetail</div>,
}));
vi.mock('@/features/parameters/ParameterPage', () => ({
  ParameterPage: () => <div data-testid="parameter-page">ParameterPage</div>,
}));
vi.mock('@/features/research/ResearchPage', () => ({
  ResearchPage: () => <div data-testid="research-page">ResearchPage</div>,
}));
vi.mock('@/features/research/PublicationPage', () => ({
  PublicationPage: () => <div data-testid="publication-page">PublicationPage</div>,
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
        <LabOpsPage />
      </AntApp>
    </PageHeaderProvider>,
  );
}

describe('LabOpsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSearch = {};
    useAuthStore.getState().reset();
  });

  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it('renders title 实验室运营 with default flows/parameters/models tabs when research disabled', () => {
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    expect(screen.getByTestId('header-title')).toHaveTextContent('实验室运营');
    expect(screen.getByTestId('tab-flows')).toHaveTextContent('实验项目');
    expect(screen.getByTestId('tab-parameters')).toHaveTextContent('衍生数据');
    expect(screen.getByTestId('tab-models')).toHaveTextContent('模型发布');
  });

  it('renders flows/research/publication tabs when research module enabled', () => {
    useAuthStore.setState({
      user: {
        id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [],
        featureFlags: { researchModule: true, researchAnalysis: false, legacyModelExecution: false },
      },
    });
    renderPage();
    expect(screen.getByTestId('tab-flows')).toHaveTextContent('实验项目');
    expect(screen.getByTestId('tab-research')).toHaveTextContent('研究分析');
    expect(screen.getByTestId('tab-publication')).toHaveTextContent('发布成果');
  });

  it('defaults to flows tab and renders ProjectList', () => {
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    expect(screen.getByTestId('active-tab')).toHaveTextContent('flows');
    expect(screen.getByTestId('project-list')).toBeInTheDocument();
  });

  it('renders ProjectDetail when project param present', () => {
    mockSearch = { tab: 'flows', project: 'proj-001' };
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    expect(screen.getByTestId('project-detail')).toBeInTheDocument();
  });

  it('switches tab by calling navigate', async () => {
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
    renderPage();
    await userEvent.click(screen.getByTestId('tab-parameters'));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/lab-ops',
      search: { tab: 'parameters' },
      replace: true,
    });
  });

  it('renders research page when tab=research and research module enabled', () => {
    mockSearch = { tab: 'research' };
    useAuthStore.setState({
      user: {
        id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [],
        featureFlags: { researchModule: true, researchAnalysis: false, legacyModelExecution: false },
      },
    });
    renderPage();
    expect(screen.getByTestId('research-page')).toBeInTheDocument();
  });
});
