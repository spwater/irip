import { Tabs } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { ExperimentalObjectPage } from '@/objects/ExperimentalObjectPage';
import { DepartmentManagement } from '@/pages/governance/DepartmentManagement';
import { EquipmentPage } from '@/equipment/EquipmentPage';
import { PageIntro } from '@/components/ui';

/** 合法的 Tab key 集合。 */
const VALID_TABS = ['departments', 'equipment', 'exp-objects'] as const;
type StandardsTab = (typeof VALID_TABS)[number];

/**
 * 实验室建设页面（设计文档第 10.4 节）
 *
 * 三个 Tab：组织机构 / 设备仪器 / 实验对象
 * 跨 Tab 的"+仪器"/"+对象"操作已改为就地打开抽屉，不再切换 Tab。
 */
export function StandardsPage(): JSX.Element {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const tabRaw = (search as Record<string, unknown>).tab;
  const activeTab: StandardsTab = (
    VALID_TABS as readonly string[]
  ).includes(typeof tabRaw === 'string' ? tabRaw : '')
    ? (tabRaw as StandardsTab)
    : 'departments';

  const handleTabChange = (key: string): void => {
    void navigate({ to: '/standards', search: { tab: key }, replace: true });
  };

  return (
    <div className="ocean-page-enter">
      <PageIntro
        index="MODULE 02 / LAB STANDARDS"
        title="实验室建设"
      />

      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={[
          {
            key: 'departments',
            label: '组织机构',
            children: <DepartmentManagement />,
          },
          {
            key: 'equipment',
            label: '设备仪器',
            children: <EquipmentPage />,
          },
          {
            key: 'exp-objects',
            label: '实验对象',
            children: <ExperimentalObjectPage />,
          },
        ]}
      />
    </div>
  );
}
