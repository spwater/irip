import { useState } from 'react';
import { Tabs } from 'antd';
import { ExperimentalObjectPage } from '@/objects/ExperimentalObjectPage';
import { DepartmentManagement } from '@/pages/governance/DepartmentManagement';
import { EquipmentPage } from '@/equipment/EquipmentPage';
import { ConstructionTrack } from '@/standards/ConstructionTrack';
import { OceanPanel, PageIntro } from '@/components/ui';

/**
 * 实验室建设页面
 *
 * 三个 Tab：组织机构 / 设备仪器 / 实验对象
 *
 * Data Ocean Phase 2 升级：
 * - PageIntro 替换 Title level={2}
 * - ConstructionTrack 展示跨 Tab 链路（组织机构 → 设备仪器 → 实验对象）
 * - Tabs 放入 OceanPanel 结构化容器
 * - 保留所有现有 state/callback/预填行为不变
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
    <div className="ocean-page">
      <PageIntro
        index="LAB / 02"
        title="实验室建设"
        description="按组织机构、设备仪器、实验对象三阶段构建实验室要素目录。"
      />

      <ConstructionTrack activeKey={activeTab} />

      <OceanPanel level="structural">
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
      </OceanPanel>
    </div>
  );
}
