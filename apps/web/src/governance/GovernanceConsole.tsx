import { useState } from 'react';
import { Row, Col, Tabs } from 'antd';
import { UsersPage } from '@/governance/UsersPage';
import { AuditPage } from '@/governance/AuditPage';
import { SystemHealthPage } from '@/governance/SystemHealthPage';
import { AIConfigPage } from '@/governance/AIConfigPage';
import { JobsPage } from '@/jobs/JobsPage';
import { PageIntro } from '@/components/ui';

/**
 * 平台治理页面 — 治理监控原型（设计文档第 10.7 节）
 *
 * 系统配置使用两区监控台：AI 配置与系统健康；1280px 以下可垂直排列。
 * destroyInactiveTabPane 行为保持不变，避免改变治理页面刷新语义。
 */
export function GovernanceConsole(): JSX.Element {
  const [activeTab, setActiveTab] = useState<string>('system-config');

  return (
    <div className="ocean-page-enter">
      <PageIntro
        index="MODULE 05 / PLATFORM GOVERNANCE"
        title="平台治理"
      />
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
