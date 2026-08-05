/**
 * EditFlowModal — 编辑任务 Modal。
 *
 * 从 FlowDetail.tsx 提取。通过 props 传递 open/onClose/回调。
 */

import { Form, Input, Select, Space, Button, Modal } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { DepartmentSelector } from '@/shared/DepartmentSelector';

export interface EditFlowModalProps {
  open: boolean;
  onOk: () => void;
  onCancel: () => void;
  confirmLoading: boolean;
  editForm: ReturnType<typeof Form.useForm>[0];
  objectOptions: { value: string; label: string; object_type: string }[];
  onNewObject: () => void;
}

export function EditFlowModal(props: EditFlowModalProps): JSX.Element {
  const { open, onOk, onCancel, confirmLoading, editForm, objectOptions, onNewObject } = props;

  return (
    <Modal
      title="编辑任务"
      open={open}
      onOk={onOk}
      onCancel={onCancel}
      confirmLoading={confirmLoading}
      okText="保存"
      cancelText="取消"
    >
      <Form form={editForm} layout="vertical">
        <Form.Item
          name="display_name"
          label="任务名称"
          rules={[{ required: true, message: '请输入任务名称' }]}
        >
          <Input placeholder="请输入任务名称" maxLength={200} />
        </Form.Item>
        <Form.Item name="department_id" label="所属单位">
          <DepartmentSelector placeholder="请选择所属单位" allowRoot={true} />
        </Form.Item>
        <Form.Item
          name="operator"
          label="执行人"
          rules={[{ required: true, message: '请输入执行人' }]}
        >
          <Input placeholder="如：宋昊" maxLength={100} />
        </Form.Item>
        <Form.Item label="实验对象">
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="experimental_object_code" noStyle>
              <Select
                placeholder="请选择实验对象"
                allowClear
                showSearch
                optionFilterProp="label"
                options={objectOptions}
                style={{ width: 'calc(100% - 40px)' }}
              />
            </Form.Item>
            <Button
              icon={<PlusOutlined />}
              onClick={onNewObject}
              title="新建实验对象"
              style={{ width: 40 }}
            />
          </Space.Compact>
        </Form.Item>
      </Form>
    </Modal>
  );
}
