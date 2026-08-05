/**
 * NewObjectModal — 新建实验对象 Modal。
 *
 * 从 FlowDetail.tsx 提取。通过 props 传递 open/onClose/回调。
 */

import { Form, Input, Select, TreeSelect, Button, Space, Modal } from 'antd';
import { PlusOutlined } from '@ant-design/icons';

export interface NewObjectModalProps {
  open: boolean;
  onCancel: () => void;
  onOk: () => void;
  confirmLoading: boolean;
  newObjectForm: ReturnType<typeof Form.useForm>[0];
  currentUserDepartmentId: string | undefined;
  objectTypeOptions: { value: string; label: string }[];
  deptOptions: { value: string; label: string }[];
  deptTreeData: unknown;
  compOptionsForObj: { value: string; label: string }[];
  onNewComponent: () => void;
}

export function NewObjectModal(props: NewObjectModalProps): JSX.Element {
  const {
    open,
    onCancel,
    onOk,
    confirmLoading,
    newObjectForm,
    currentUserDepartmentId,
    objectTypeOptions,
    deptOptions,
    deptTreeData,
    compOptionsForObj,
    onNewComponent,
  } = props;

  return (
    <Modal
      title="新建实验对象"
      open={open}
      onCancel={onCancel}
      footer={
        <Space>
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" onClick={onOk} loading={confirmLoading}>
            创建
          </Button>
        </Space>
      }
      width={600}
      destroyOnClose
    >
      <Form form={newObjectForm} layout="vertical" initialValues={{ department_id: currentUserDepartmentId }}>
        <Form.Item name="display_name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="如：铝合金" maxLength={200} />
        </Form.Item>
        <Form.Item name="object_type" label="类型" rules={[{ required: true, message: '请选择类型' }]}>
          <Select placeholder="选择实验对象类型" options={objectTypeOptions} />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea placeholder="对象描述（可选）" rows={3} maxLength={2000} />
        </Form.Item>
        <Form.Item name="department_id" label="所属单位" rules={[{ required: true, message: '请选择所属单位' }]}>
          <Select placeholder="选择所属单位" showSearch optionFilterProp="label" options={deptOptions} />
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
                options={compOptionsForObj}
                allowClear
                style={{ width: 'calc(100% - 40px)' }}
              />
            </Form.Item>
            <Button icon={<PlusOutlined />} onClick={onNewComponent} title="新建数据接口" style={{ width: 40 }} />
          </Space.Compact>
        </Form.Item>
      </Form>
    </Modal>
  );
}
