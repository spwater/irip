import { useMemo, useState } from 'react';
import { Row, Col } from 'antd';
import { UsersPage } from '@/features/governance/UsersPage';
import { AuditPage } from '@/features/governance/AuditPage';
import { SystemHealthPage } from '@/features/governance/SystemHealthPage';
import { AIConfigPage } from '@/features/governance/AIConfigPage';
import { DatabaseBackupPage } from '@/features/governance/DatabaseBackupPage';
import { JobsPage } from '@/features/jobs/JobsPage';
import { DataTransferPanel } from '@/features/governance/DataTransferPanel';
import { RootDataStats } from '@/features/governance/RootDataStats';
import { usePageHeaderRegistration } from '@/app/PageHeaderContext';
import { useAuthStore } from '@/features/auth/AuthProvider';

/**
 * 平台治理页面
 *
 * Tab 切换和页面标题注册到 AppShell Header。
 * irip-ai-collab: lab_director 可见「用户管理」Tab（角色分配），不可见其他管理 Tab。
 *
 * M-09 整改：使用 usePageHeaderRegistration，unmount 时清空 header。
 */
export function GovernanceConsole(): JSX.Element {
  const [activeTab, setActiveTab] = useState<string>('system-config');
  const user = useAuthStore((s) => s.user);
  const isAdmin: boolean = user?.roles?.includes('platform_administrator') ?? false;
  const isLabDirector: boolean = user?.roles?.includes('lab_director') ?? false;

  const tabs = useMemo(() => {
    const items: Array<{ key: string; label: string }> = [];
    if (isAdmin) {
      items.push({ key: 'system-config', label: '系统配置' });
    }
    // irip-ai-collab: platform_administrator 和 lab_director 均可见用户管理
    if (isAdmin || isLabDirector) {
      items.push({ key: 'users', label: '用户管理' });
    }
    if (isAdmin) {
      items.push({ key: 'audit', label: '审计事件' });
      items.push({ key: 'jobs', label: '作业中心' });
      items.push({ key: 'data-transfer', label: '数据移交' });
      items.push({ key: 'db-backup', label: '数据库备份' });
    }
    return items;
  }, [isAdmin, isLabDirector]);

  usePageHeaderRegistration(
    {
      index: 'MODULE 05 / PLATFORM GOVERNANCE',
      title: '平台治理',
      tabs,
      activeTab,
      onTabChange: (key) => setActiveTab(key),
    },
    [activeTab, tabs],
  );

  return (
    <div className="ocean-page-enter" key={activeTab}>
      {activeTab === 'system-config' && isAdmin && (
        <Row gutter={24}>
          <Col xs={24} lg={12}>
            <div style={{ marginTop: -8 }}>
              <AIConfigPage />
            </div>
          </Col>
          <Col xs={24} lg={12}>
            <SystemHealthPage />
          </Col>
        </Row>
      )}
      {activeTab === 'users' && <UsersPage />}
      {activeTab === 'audit' && isAdmin && <AuditPage />}
      {activeTab === 'jobs' && isAdmin && <JobsPage />}
      {activeTab === 'data-transfer' && isAdmin && (
        <Row gutter={24}>
          <Col xs={24} lg={14}>
            <DataTransferPanel />
          </Col>
          <Col xs={24} lg={10}>
            <RootDataStats />
          </Col>
        </Row>
      )}
      {activeTab === 'db-backup' && isAdmin && <DatabaseBackupPage />}
    </div>
  );
}
