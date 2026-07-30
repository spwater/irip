import { useState } from 'react';
import { Row, Col } from 'antd';
import { UsersPage } from '@/features/governance/UsersPage';
import { AuditPage } from '@/features/governance/AuditPage';
import { SystemHealthPage } from '@/features/governance/SystemHealthPage';
import { AIConfigPage } from '@/features/governance/AIConfigPage';
import { JobsPage } from '@/features/jobs/JobsPage';
import { usePageHeaderRegistration } from '@/app/PageHeaderContext';

/**
 * 平台治理页面
 *
 * Tab 切换和页面标题注册到 AppShell Header。
 *
 * M-09 整改：使用 usePageHeaderRegistration，unmount 时清空 header。
 */
export function GovernanceConsole(): JSX.Element {
  const [activeTab, setActiveTab] = useState<string>('system-config');

  usePageHeaderRegistration(
    {
      index: 'MODULE 05 / PLATFORM GOVERNANCE',
      title: '平台治理',
      tabs: [
        { key: 'system-config', label: '系统配置' },
        { key: 'users', label: '用户管理' },
        { key: 'audit', label: '审计事件' },
        { key: 'jobs', label: '作业中心' },
      ],
      activeTab,
      onTabChange: (key) => setActiveTab(key),
    },
    [activeTab],
  );

  return (
    <div className="ocean-page-enter" key={activeTab}>
      {activeTab === 'system-config' && (
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
      {activeTab === 'audit' && <AuditPage />}
      {activeTab === 'jobs' && <JobsPage />}
    </div>
  );
}
