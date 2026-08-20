/** ExperimentalObjectPage — 实验对象管理页面（编排层，从 993 行精简）。 */

import { useEffect, useState } from 'react';
import { Button, Form, Select, Space } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { FRESH_FORM_VALUES } from '@/shared/component-utils';
import { apiGetObject } from '@/api/standards-objects';
import type { IndustrialObject } from '@/api/types';
import { useObjectQueries } from './experimental-object/hooks/useObjectQueries';
import { useObjectMutations } from './experimental-object/hooks/useObjectMutations';
import { ObjectListTable } from './experimental-object/components/ObjectListTable';
import { ObjectFormModal } from './experimental-object/components/ObjectFormModal';
import { TypeManagerModal } from './experimental-object/components/TypeManagerModal';
import { NewComponentModal } from './experimental-object/components/NewComponentModal';
import { buildTreeData } from './experimental-object/utils/buildTreeData';
import { submitComponentForm } from './experimental-object/utils/submitComponentForm';
import type { TreeRow } from './experimental-object/types';

export function ExperimentalObjectPage({
  presetEquipmentId,
  onPresetConsumed,
}: {
  presetEquipmentId?: string;
  onPresetConsumed?: () => void;
}): JSX.Element {
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<IndustrialObject | null>(null);
  const [typeMgrOpen, setTypeMgrOpen] = useState(false);
  const [compCreateModalOpen, setCompCreateModalOpen] = useState(false);
  const [compAdvancedMode, setCompAdvancedMode] = useState(false);
  const [form] = Form.useForm();
  const [compForm] = Form.useForm();

  const queries = useObjectQueries();
  const mutations = useObjectMutations({
    form, compForm, setModalOpen, setEditingItem, setCompCreateModalOpen,
  });

  useEffect(() => {
    if (presetEquipmentId) {
      setEditingItem(null);
      form.resetFields();
      form.setFieldsValue({ visible_departments: [] });
      setModalOpen(true);
      onPresetConsumed?.();
    }
  }, [presetEquipmentId]);

  let filteredItems = queries.items;
  if (deptFilter) {
    filteredItems = queries.items.filter((o) => o.department_id === deptFilter);
  }

  const treeData: TreeRow[] = buildTreeData(
    filteredItems, queries.objectTypeData, typeFilter,
  );

  const handleCreate = (): void => {
    setEditingItem(null);
    form.resetFields();
    form.setFieldsValue({ visible_departments: [], component_id: undefined });
    setModalOpen(true);
  };

  const handleEdit = async (record: IndustrialObject): Promise<void> => {
    const detail = await apiGetObject(record.id);
    setEditingItem(record);
    form.setFieldsValue({
      code: record.code,
      display_name: detail.display_name,
      object_type: record.object_type,
      description: detail.description ?? '',
      department_id: detail.department_id ?? undefined,
      visible_departments: detail.visible_departments ?? [],
      component_id: detail.component_id ?? undefined,
    });
    setModalOpen(true);
  };

  const handleSubmit = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      if (editingItem) {
        mutations.updateMutation.mutate({
          id: editingItem.id,
          body: {
            display_name: values.display_name,
            description: values.description ?? null,
            object_type: values.object_type,
            department_id: values.department_id,
            visible_departments: values.visible_departments ?? [],
            component_id: values.component_id ?? null,
          },
        });
      } else {
        mutations.createMutation.mutate({
          display_name: values.display_name,
          object_type: values.object_type,
          description: values.description,
          department_id: values.department_id,
          visible_departments: values.visible_departments ?? [],
          component_id: values.component_id || undefined,
        });
      }
    } catch {
      // 表单校验失败
    }
  };

  const handleToggleStatus = (record: IndustrialObject): void => {
    mutations.statusMutation.mutate({
      id: record.id,
      body: { status: record.status === 'active' ? 'inactive' : 'active' },
    });
  };

  const handleDelete = (): void => {
    if (!editingItem) return;
    mutations.deleteMutation.mutate(editingItem.id);
  };

  const closeModal = (): void => {
    setModalOpen(false);
    setEditingItem(null);
    form.resetFields();
  };

  const openNewComponent = (): void => {
    const curDept = form.getFieldValue('department_id') as string | undefined;
    const curName = form.getFieldValue('display_name') as string | undefined;
    compForm.resetFields();
    setCompAdvancedMode(false);
    compForm.setFieldsValue({
      ...FRESH_FORM_VALUES,
      tool_type: 'llm_converter',
      department_id: curDept,
      display_name: curName ? `${curName}接口` : undefined,
    });
    setCompCreateModalOpen(true);
  };

  const handleNewCompOk = (): void => {
    void submitComponentForm(compForm, compAdvancedMode, mutations.publishCompMutation.mutate);
  };

  const closeCompModal = (): void => {
    setCompCreateModalOpen(false);
    compForm.resetFields();
    setCompAdvancedMode(false);
  };

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          新建实验对象
        </Button>
        <Button onClick={() => setTypeMgrOpen(true)}>
          类型管理
        </Button>
        <Select
          placeholder="类型筛选"
          style={{ width: 140 }}
          value={typeFilter ?? '__all__'}
          onChange={(val: string) => setTypeFilter(val === '__all__' ? undefined : val)}
          options={[{ value: '__all__', label: '全部' }, ...queries.objectTypeOptions]}
        />
        <Select
          placeholder="关联单位筛选"
          style={{ width: 160 }}
          value={deptFilter ?? '__all__'}
          onChange={(val: string) => {
            setDeptFilter(val === '__all__' ? undefined : val);
          }}
          options={[{ value: '__all__', label: '全部' }, ...queries.deptOptions]}
        />
      </Space>

      <ObjectListTable
        treeData={treeData}
        loading={queries.isLoading}
        deptMap={queries.deptMap}
        componentMap={queries.componentMap}
        onEdit={handleEdit}
        onToggleStatus={handleToggleStatus}
      />

      <ObjectFormModal
        open={modalOpen}
        editingItem={editingItem}
        form={form}
        objectTypeOptions={queries.objectTypeOptions}
        allDeptOptions={queries.allDeptOptions}
        deptTreeData={queries.deptTreeData}
        componentOptions={queries.componentOptions}
        onCancel={closeModal}
        onOk={handleSubmit}
        onDelete={handleDelete}
        createPending={mutations.createMutation.isPending}
        updatePending={mutations.updateMutation.isPending}
        deletePending={mutations.deleteMutation.isPending}
        onNewComponent={openNewComponent}
      />

      <TypeManagerModal
        open={typeMgrOpen}
        onCancel={() => setTypeMgrOpen(false)}
      />

      <NewComponentModal
        open={compCreateModalOpen}
        onOk={handleNewCompOk}
        onCancel={closeCompModal}
        confirmLoading={mutations.publishCompMutation.isPending}
        compForm={compForm}
        compAdvancedMode={compAdvancedMode}
        setCompAdvancedMode={setCompAdvancedMode}
        ingestionToolOptions={queries.ingestionToolOptions}
        deptOptions={queries.deptOptions}
        deptTreeData={queries.deptTreeData}
      />
    </div>
  );
}
