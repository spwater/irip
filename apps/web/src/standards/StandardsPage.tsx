import { useState } from 'react';
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
 * PageIntro 下增加"组织机构 → 设备仪器 → 实验对象"建设链路提示。
 * 数据接口 Tab 已移至平台应用页面。
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

  const [presetDeptId, setPresetDeptId] = useState<string | undefined>(undefined);
  const [presetEquipmentId, setPresetEquipmentId] = useState<string | undefined>(undefined);

  const handleTabChange = (key: string): void => {
    void navigate({ to: '/standards', search: { tab: key }, replace: true });
  };

  // 从组织机构跳到设备仪器，预填 department_id
  const handleAddEquipmentForDept = (deptId: string): void => {
    setPresetDeptId(deptId);
    setPresetEquipmentId(undefined);
    handleTabChange('equipment');
  };

  // 从设备仪器跳到实验对象，预填 equipment_id
  const handleAddObjectForEquipment = (equipmentId: string): void => {
    setPresetEquipmentId(equipmentId);
    setPresetDeptId(undefined);
    handleTabChange('exp-objects');
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
            children: <DepartmentManagement onAddEquipment={handleAddEquipmentForDept} />,
          },
          {
            key: 'equipment',
            label: '设备仪器',
            children: (
              <EquipmentPage
                presetDeptId={presetDeptId}
                onPresetDeptIdConsumed={() => setPresetDeptId(undefined)}
                onAddObject={handleAddObjectForEquipment}
              />
            ),
          },
          {
            key: 'exp-objects',
            label: '实验对象',
            children: (
              <ExperimentalObjectPage
                presetEquipmentId={presetEquipmentId}
                onPresetConsumed={() => setPresetEquipmentId(undefined)}
              />
            ),
          },
        ]}
      />
    </div>
  );
}
