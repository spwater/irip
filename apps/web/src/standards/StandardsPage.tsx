import { useNavigate, useSearch } from '@tanstack/react-router';
import { ExperimentalObjectPage } from '@/objects/ExperimentalObjectPage';
import { DepartmentManagement } from '@/pages/governance/DepartmentManagement';
import { EquipmentPage } from '@/equipment/EquipmentPage';
import { usePageHeaderRegistration } from '@/app/PageHeaderContext';

/** 合法的 Tab key 集合。 */
const VALID_TABS = ['departments', 'equipment', 'exp-objects'] as const;
type StandardsTab = (typeof VALID_TABS)[number];

/**
 * 实验室建设页面
 *
 * 三个 Tab：组织机构 / 设备仪器 / 实验对象
 * Tab 切换和页面标题注册到 AppShell Header。
 *
 * M-09 整改：使用 usePageHeaderRegistration，unmount 时清空 header。
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

  usePageHeaderRegistration(
    {
      index: 'MODULE 02 / LAB STANDARDS',
      title: '实验室建设',
      tabs: [
        { key: 'departments', label: '组织机构' },
        { key: 'equipment', label: '设备仪器' },
        { key: 'exp-objects', label: '实验对象' },
      ],
      activeTab,
      onTabChange: handleTabChange,
    },
    [activeTab],
  );

  return (
    <div className="ocean-page-enter" key={activeTab}>
      {activeTab === 'departments' && <DepartmentManagement />}
      {activeTab === 'equipment' && <EquipmentPage />}
      {activeTab === 'exp-objects' && <ExperimentalObjectPage />}
    </div>
  );
}
