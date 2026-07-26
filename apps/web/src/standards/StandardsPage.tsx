import { useState } from 'react';
import { Tabs, Typography } from 'antd';
import { ExperimentalObjectPage } from '@/objects/ExperimentalObjectPage';
import { DepartmentManagement } from '@/pages/governance/DepartmentManagement';
import { EquipmentPage } from '@/equipment/EquipmentPage';

const { Title } = Typography;

/**
 * 实验室建设页面
 *
 * 三个 Tab：组织机构 / 设备仪器 / 实验对象
 */
export function StandardsPage(): JSX.Element {
  const [activeTab, setActiveTab] = useState('departments');
  const [presetDeptId, setPresetDeptId] = useState<string | undefined>(undefined);
  const [presetEquipmentId, setPresetEquipmentId] = useState<string | undefined>(undefined);

  // 从组织机构跳到设备仪器，预填 department_id
  const handleAddEquipmentForDept = (deptId: string): void => {
    setPresetDeptId(deptId);
    setPresetEquipmentId(undefined);
    setActiveTab('equipment');
  };

  // 从设备仪器跳到实验对象，预填 equipment_id
  const handleAddObjectForEquipment = (equipmentId: string): void => {
    setPresetEquipmentId(equipmentId);
    setPresetDeptId(undefined);
    setActiveTab('exp-objects');
  };

  return (
    <div>
      <Title level={2}>实验室建设</Title>
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key)}
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
