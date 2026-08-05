/**
 * CreateFlowModal — 新建任务 Modal。
 *
 * 从 FlowDetail.tsx 提取。通过 props 传递 open/onClose/回调。
 */

import { Col, Form, Input, Row, Select, Space, Button, Modal } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { DepartmentSelector } from '@/shared/DepartmentSelector';

export interface CreateFlowModalProps {
  open: boolean;
  onOk: () => void;
  onCancel: () => void;
  confirmLoading: boolean;
  createForm: ReturnType<typeof Form.useForm>[0];
  selectedType: string | undefined;
  setSelectedType: (val: string | undefined) => void;
  objectTypeOptions: { value: string; label: string }[];
  objectOptions: { value: string; label: string; object_type: string }[];
  objMap: Map<string, { display_name: string }>;
  onNewObject: () => void;
}

export function CreateFlowModal(props: CreateFlowModalProps): JSX.Element {
  const {
    open,
    onOk,
    onCancel,
    confirmLoading,
    createForm,
    selectedType,
    setSelectedType,
    objectTypeOptions,
    objectOptions,
    objMap,
    onNewObject,
  } = props;

  return (
    <Modal
      title="新建任务"
      open={open}
      onOk={onOk}
      onCancel={onCancel}
      confirmLoading={confirmLoading}
      okText="创建"
      cancelText="取消"
    >
      <Form form={createForm} layout="vertical">
        <Row gutter={16}>
          <Col span={6}>
            <Form.Item label="类型">
              <Select
                placeholder="全部"
                allowClear
                value={selectedType}
                onChange={(val: string | undefined) => {
                  setSelectedType(val);
                  const currentObj = createForm.getFieldValue('experimental_object_code');
                  if (currentObj) {
                    const obj = objMap.get(currentObj);
                    if (obj && val && obj.display_name && false) {
                      // type mismatch check — simplified, handled in parent
                      createForm.setFieldsValue({ experimental_object_code: undefined });
                    }
                  }
                }}
                options={objectTypeOptions}
              />
            </Form.Item>
          </Col>
          <Col span={18}>
            <Form.Item label="实验对象">
              <Space.Compact style={{ width: '100%' }}>
                <Form.Item name="experimental_object_code" noStyle>
                  <Select
                    placeholder="请选择实验对象"
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    options={objectOptions.filter((o) => !selectedType || o.object_type === selectedType)}
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
          </Col>
        </Row>
        <Form.Item
          name="department_id"
          label="所属单位"
          rules={[{ required: true, message: '请选择所属单位' }]}
        >
          <DepartmentSelector placeholder="请选择所属单位" allowRoot={true} />
        </Form.Item>
        <Form.Item
          name="display_name"
          label="任务名称"
          rules={[{ required: true, message: '请输入任务名称' }]}
        >
          <Input placeholder="如：篔冷机分析任务" maxLength={200} />
        </Form.Item>
        <Form.Item
          name="operator"
          label="执行人"
          rules={[{ required: true, message: '请输入执行人' }]}
        >
          <Input placeholder="如：宋昊" maxLength={100} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
