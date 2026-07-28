import { useState } from 'react';
import { Tabs, Typography } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { ExperimentalObjectPage } from '@/objects/ExperimentalObjectPage';
import { DepartmentManagement } from '@/pages/governance/DepartmentManagement';
import { EquipmentPage } from '@/equipment/EquipmentPage';
import { ComponentsPage } from '@/components/ComponentsPage';

const { Title } = Typography;

/** 合法的 Tab key 集合。 */
const VALID_TABS = ['departments', 'equipment', 'exp-objects', 'components'] as const;
type StandardsTab = (typeof VALID_TABS)[number];

/**
 * 实验室建设页面
 *
 * 四个 Tab：组织机构 / 设备仪器 / 实验对象 / 数据接口
 */
export function StandardsPage(): JSX.Element {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const tabRaw = (search as Record<string, unknown>).tab;
  const prefillObjectRaw = (search as Record<string, unknown>).prefill_object;
  const prefillObject: string | undefined =
    typeof prefillObjectRaw === 'string' ? prefillObjectRaw : undefined;
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
    <div>
      <Title level={2}>实验室建设</Title>
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
          {
            key: 'components',
            label: '数据接口',
            children: <ComponentsPage prefillObject={prefillObject} />,
          },
        ]}
      />
    </div>
  );
}
