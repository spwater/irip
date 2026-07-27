import { useState } from 'react';
import { Row, Col, Tabs, Typography } from 'antd';
import { UsersPage } from '@/governance/UsersPage';
import { AuditPage } from '@/governance/AuditPage';
import { SystemHealthPage } from '@/governance/SystemHealthPage';
import { AIConfigPage } from '@/governance/AIConfigPage';
import { JobsPage } from '@/jobs/JobsPage';

const { Title } = Typography;

/**
 * 平台治理页面 — Tabs 布局
 */
export function GovernanceConsole(): JSX.Element {
  const [activeTab, setActiveTab] = useState<string>('system-config');

  return (
    <div>
      <Title level={2}>平台治理</Title>
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key)}
        destroyInactiveTabPane
        items={[
          {
            key: 'system-config',
            label: '系统配置',
            children: (
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
            ),
          },
          {
            key: 'users',
            label: '用户管理',
            children: <UsersPage />,
          },
          {
            key: 'audit',
            label: '审计事件',
            children: <AuditPage />,
          },
          {
            key: 'jobs',
            label: '作业中心',
            children: <JobsPage />,
          },
        ]}
      />
    </div>
  );
}
