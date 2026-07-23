import { useState } from 'react';
import { Tabs, Typography } from 'antd';
import { ExperimentalObjectPage } from '@/objects/ExperimentalObjectPage';
import { DepartmentManagement } from '@/pages/governance/DepartmentManagement';
import { EquipmentPage } from '@/equipment/EquipmentPage';
import { ComponentsPage } from '@/components/ComponentsPage';
import { ModelsPage } from '@/models/ModelsPage';

const { Title, Paragraph } = Typography;

/**
 * 实验室建设页面
 *
 * 五个 Tab：组织机构 / 设备仪器 / 实验对象 / 工具箱 / 模型管理
 * 支持跨 Tab 快捷操作：
 * - 组织机构行点"+仪器" → 切到设备 Tab，预填所属机构
 * - 设备仪器行点"+对象" → 切到实验对象 Tab，预填关联设备
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
      <Paragraph type="secondary">组织机构、设备仪器、实验对象、工具箱与模型管理</Paragraph>
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
          {
            key: 'components',
            label: '工具箱',
            children: <ComponentsPage />,
          },
          {
            key: 'models',
            label: '模型管理',
            children: <ModelsPage />,
          },
        ]}
      />
    </div>
  );
}
