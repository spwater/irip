import { useState } from 'react';
import { Tabs, Typography } from 'antd';
import { UsersPage } from '@/governance/UsersPage';
import { ScopeGrantsPage } from '@/governance/ScopeGrantsPage';
import { AuditPage } from '@/governance/AuditPage';
import { SystemHealthPage } from '@/governance/SystemHealthPage';
import { AIConfigPage } from '@/governance/AIConfigPage';
import { JobsPage } from '@/jobs/JobsPage';

const { Title, Paragraph } = Typography;

/**
 * 平台治理页面 — Tabs 布局
 */
export function GovernanceConsole(): JSX.Element {
  const [activeTab, setActiveTab] = useState<string>('overview');

  return (
    <div>
      <Title level={2}>平台治理</Title>
      <Paragraph type="secondary">用户管理、范围授权、审计事件、系统健康、AI 配置与作业中心</Paragraph>
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key)}
        destroyInactiveTabPane
        items={[
          {
            key: 'overview',
            label: '概览',
            children: (
              <Paragraph type="secondary">
                平台治理提供用户管理、范围授权、审计事件和系统健康监控功能。
                请从上方选项卡选择需要的管理功能。
              </Paragraph>
            ),
          },
          {
            key: 'users',
            label: '用户管理',
            children: <UsersPage />,
          },
          {
            key: 'grants',
            label: '范围授权',
            children: <ScopeGrantsPage />,
          },
          {
            key: 'audit',
            label: '审计事件',
            children: <AuditPage />,
          },
          {
            key: 'health',
            label: '系统健康',
            children: <SystemHealthPage />,
          },
          {
            key: 'ai-config',
            label: 'AI 配置',
            children: <AIConfigPage />,
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
