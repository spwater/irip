import { useState } from 'react';
import { Tabs } from 'antd';
import { UsersPage } from '@/governance/UsersPage';
import { AuditPage } from '@/governance/AuditPage';
import { SystemHealthPage } from '@/governance/SystemHealthPage';
import { AIConfigPage } from '@/governance/AIConfigPage';
import { JobsPage } from '@/jobs/JobsPage';
import { PageIntro, OceanPanel } from '@/components/ui';

/**
 * 平台治理页面 — Tabs 布局
 *
 * 保留 destroyInactiveTabPane 行为和初始 system-config 键。
 * 系统配置 Tab 使用 CSS grid 两列布局，1280px 以下堆叠。
 */
export function GovernanceConsole(): JSX.Element {
  const [activeTab, setActiveTab] = useState<string>('system-config');

  return (
    <div>
      <PageIntro
        index="GOV / 01"
        title="平台治理"
        description="系统配置、用户管理、审计事件和作业中心。"
      />
      <OceanPanel level="strong">
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key)}
          destroyInactiveTabPane
          items={[
            {
              key: 'system-config',
              label: '系统配置',
              children: (
                <div className="ocean-gov-config-grid">
                  <AIConfigPage />
                  <SystemHealthPage />
                </div>
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
      </OceanPanel>
    </div>
  );
}
