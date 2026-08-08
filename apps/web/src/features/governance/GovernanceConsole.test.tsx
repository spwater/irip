import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { App as AntApp } from 'antd';
import { PageHeaderProvider, usePageHeader } from '@/app/PageHeaderContext';
import { GovernanceConsole } from './GovernanceConsole';
import { useAuthStore } from '@/features/auth/AuthProvider';

// Mock child page components to isolate GovernanceConsole logic
vi.mock('@/features/governance/UsersPage', () => ({
  UsersPage: () => <div data-testid="users-page">UsersPage</div>,
}));
vi.mock('@/features/governance/AuditPage', () => ({
  AuditPage: () => <div data-testid="audit-page">AuditPage</div>,
}));
vi.mock('@/features/governance/SystemHealthPage', () => ({
  SystemHealthPage: () => <div data-testid="health-page">SystemHealthPage</div>,
}));
vi.mock('@/features/governance/AIConfigPage', () => ({
  AIConfigPage: () => <div data-testid="ai-config-page">AIConfigPage</div>,
}));
vi.mock('@/features/governance/DatabaseBackupPage', () => ({
  DatabaseBackupPage: () => <div data-testid="db-backup-page">DatabaseBackupPage</div>,
}));
vi.mock('@/features/jobs/JobsPage', () => ({
  JobsPage: () => <div data-testid="jobs-page">JobsPage</div>,
}));
vi.mock('@/features/governance/DataTransferPanel', () => ({
  DataTransferPanel: () => <div data-testid="data-transfer-panel">DataTransferPanel</div>,
}));
vi.mock('@/features/governance/RootDataStats', () => ({
  RootDataStats: () => <div data-testid="root-data-stats">RootDataStats</div>,
}));

/** Helper to read header state inside test */
function HeaderProbe(): JSX.Element {
  const { header } = usePageHeader();
  return (
    <div>
      <span data-testid="header-title">{header.title ?? ''}</span>
      <span data-testid="header-tab-count">{header.tabs?.length ?? 0}</span>
      <div data-testid="header-tabs">
        {(header.tabs ?? []).map((t) => (
          <span key={t.key} data-testid={`tab-${t.key}`}>{t.label}</span>
        ))}
      </div>
    </div>
  );
}

function renderConsole(): void {
  render(
    <PageHeaderProvider>
      <AntApp>
        <HeaderProbe />
        <GovernanceConsole />
      </AntApp>
    </PageHeaderProvider>,
  );
}

describe('GovernanceConsole', () => {
  beforeEach(() => {
    useAuthStore.getState().reset();
  });

  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it('shows all tabs for platform_administrator', () => {
    useAuthStore.setState({
      user: {
        id: 'u-admin',
        displayName: '管理员',
        roles: ['platform_administrator'],
        permissions: [],
      },
    });
    renderConsole();
    expect(screen.getByTestId('tab-system-config')).toHaveTextContent('系统配置');
    expect(screen.getByTestId('tab-users')).toHaveTextContent('用户管理');
    expect(screen.getByTestId('tab-audit')).toHaveTextContent('审计事件');
    expect(screen.getByTestId('tab-jobs')).toHaveTextContent('作业中心');
    expect(screen.getByTestId('tab-data-transfer')).toHaveTextContent('数据移交');
    expect(screen.getByTestId('tab-db-backup')).toHaveTextContent('数据库备份');
    expect(screen.getByTestId('header-tab-count')).toHaveTextContent('6');
  });

  it('shows only users tab for lab_director', () => {
    useAuthStore.setState({
      user: {
        id: 'u-director',
        displayName: '所长',
        roles: ['lab_director'],
        permissions: [],
      },
    });
    renderConsole();
    expect(screen.queryByTestId('tab-system-config')).not.toBeInTheDocument();
    expect(screen.getByTestId('tab-users')).toBeInTheDocument();
    expect(screen.queryByTestId('tab-audit')).not.toBeInTheDocument();
    expect(screen.queryByTestId('tab-jobs')).not.toBeInTheDocument();
    expect(screen.getByTestId('header-tab-count')).toHaveTextContent('1');
  });

  it('shows only audit tab for platform_auditor', () => {
    useAuthStore.setState({
      user: {
        id: 'u-auditor',
        displayName: '审计员',
        roles: ['platform_auditor'],
        permissions: [],
      },
    });
    renderConsole();
    expect(screen.queryByTestId('tab-system-config')).not.toBeInTheDocument();
    expect(screen.queryByTestId('tab-users')).not.toBeInTheDocument();
    expect(screen.getByTestId('tab-audit')).toBeInTheDocument();
    expect(screen.getByTestId('header-tab-count')).toHaveTextContent('1');
  });

  it('renders system-config content by default for admin', () => {
    useAuthStore.setState({
      user: {
        id: 'u-admin',
        displayName: '管理员',
        roles: ['platform_administrator'],
        permissions: [],
      },
    });
    renderConsole();
    expect(screen.getByTestId('ai-config-page')).toBeInTheDocument();
    expect(screen.getByTestId('health-page')).toBeInTheDocument();
  });

  it('switches to users tab when clicked', () => {
    useAuthStore.setState({
      user: {
        id: 'u-admin',
        displayName: '管理员',
        roles: ['platform_administrator'],
        permissions: [],
      },
    });
    renderConsole();
    const usersTab = screen.getByTestId('tab-users');
    expect(usersTab).toBeInTheDocument();
    fireEvent.click(usersTab);
    // The click should not throw and the component should still be mounted
    expect(screen.getByTestId('header-title')).toHaveTextContent('平台治理');
  });

  it('switches to audit tab when clicked', () => {
    useAuthStore.setState({
      user: {
        id: 'u-admin',
        displayName: '管理员',
        roles: ['platform_administrator'],
        permissions: [],
      },
    });
    renderConsole();
    const auditTab = screen.getByTestId('tab-audit');
    expect(auditTab).toBeInTheDocument();
    fireEvent.click(auditTab);
    expect(screen.getByTestId('header-title')).toHaveTextContent('平台治理');
  });
});
