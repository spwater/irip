/**
 * ObjectFormModal — 实验对象创建/编辑 Modal。
 *
 * 从 ExperimentalObjectPage.tsx 提取。通过 props 传递 form 实例、数据和回调。
 * 创建与编辑共用同一弹窗，通过 editingItem 区分。
 */

import type { FormInstance } from 'antd';
import { Button, Form, Input, Modal, Popconfirm, Select, Space, TreeSelect } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { IndustrialObject } from '@/api/types';

export interface ObjectFormModalProps {
  open: boolean;
  editingItem: IndustrialObject | null;
  form: FormInstance;
  objectTypeOptions: { value: string; label: string }[];
  allDeptOptions: { value: string; label: string }[];
  deptTreeData: unknown;
  componentOptions: { value: string; label: string }[];
  equipmentOptions: { value: string; label: string }[];
  onCancel: () => void;
  onOk: () => void;
  onDelete: () => void;
  createPending: boolean;
  updatePending: boolean;
  deletePending: boolean;
  onNewComponent: () => void;
}

export function ObjectFormModal(props: ObjectFormModalProps): JSX.Element {
  const {
    open,
    editingItem,
    form,
    objectTypeOptions,
    allDeptOptions,
    deptTreeData,
    componentOptions,
    equipmentOptions,
    onCancel,
    onOk,
    onDelete,
    createPending,
    updatePending,
    deletePending,
    onNewComponent,
  } = props;

  return (
    <Modal
      title={editingItem ? '编辑实验对象' : '新建实验对象'}
      open={open}
      onCancel={onCancel}
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          {editingItem ? (
            <Popconfirm
              title="确定删除该实验对象？"
              description="此操作不可恢复"
              onConfirm={onDelete}
              okText="确定删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button
                danger
                type="primary"
                loading={deletePending}
              >
                删除对象
              </Button>
            </Popconfirm>
          ) : (
            <span />
          )}
          <Space>
            <Button onClick={onCancel}>
              取消
            </Button>
            <Button
              type="primary"
              onClick={onOk}
              loading={createPending || updatePending}
            >
              保存
            </Button>
          </Space>
        </div>
      }
      width={600}
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="display_name"
          label="名称"
          rules={[{ required: true, message: '请输入名称' }]}
        >
          <Input placeholder="如：铝合金" maxLength={200} />
        </Form.Item>
        <Form.Item
          name="object_type"
          label="类型"
          rules={[{ required: true, message: '请选择类型' }]}
        >
          <Select
            placeholder="选择实验对象类型"
            options={objectTypeOptions}
          />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea
            placeholder="对象描述（可选）"
            rows={3}
            maxLength={2000}
          />
        </Form.Item>
        <Form.Item name="department_id" label="所属单位" rules={[{ required: true, message: '请选择所属单位' }]}>
          <Select
            placeholder="选择所属单位"
            showSearch
            optionFilterProp="label"
            options={allDeptOptions}
          />
        </Form.Item>
        <Form.Item
          name="visible_departments"
          label="可见单位"
          tooltip="选填。默认按部门层级可见（上级可看下级、下级可看上级）。如需对其他部门可见，请在此添加。"
        >
          <TreeSelect
            treeData={deptTreeData as never}
            treeCheckable
            treeDefaultExpandAll
            showSearch
            treeNodeFilterProp="title"
            placeholder="不选则按部门层级默认可见"
            allowClear
            style={{ width: '100%' }}
            maxTagCount={5}
          />
        </Form.Item>
        <Form.Item label="数据接口">
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="component_id" noStyle>
              <Select
                placeholder="选择数据接口（可选）"
                showSearch
                optionFilterProp="label"
                options={componentOptions}
                allowClear
                style={{ width: 'calc(100% - 40px)' }}
              />
            </Form.Item>
            <Button
              icon={<PlusOutlined />}
              onClick={onNewComponent}
              title="新建数据接口"
              style={{ width: 40 }}
            />
          </Space.Compact>
        </Form.Item>
        <Form.Item
          name="equipment_id"
          label="设备仪器"
          tooltip="选填。将该对象关联到具体的设备仪器，用于数据溯源时的设备归属。"
        >
          <Select
            placeholder="选择设备仪器（可选）"
            showSearch
            optionFilterProp="label"
            options={equipmentOptions}
            allowClear
            style={{ width: '100%' }}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
