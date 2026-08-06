/**
 * 创建工作空间对话框
 *
 * 包含名称输入 + 研究问题文本输入。
 * 确定后调用 apiCreateWorkspace。
 */
import { useState } from 'react';
import { Modal, Form, Input, message } from 'antd';
import { apiCreateWorkspace } from '@/api/research';

interface CreateWorkspaceModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

export function CreateWorkspaceModal({ open, onClose, onCreated }: CreateWorkspaceModalProps): JSX.Element {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      setLoading(true);
      await apiCreateWorkspace({
        name: values.name,
        question_text: values.question_text,
      });
      message.success('工作空间已创建');
      form.resetFields();
      onCreated();
    } catch (err) {
      if (err instanceof Error && err.message) {
        message.error(`创建失败：${err.message}`);
      }
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = () => {
    form.resetFields();
    onClose();
  };

  return (
    <Modal
      title="新建研究工作空间"
      open={open}
      onOk={handleOk}
      onCancel={handleCancel}
      confirmLoading={loading}
      okText="创建"
      cancelText="取消"
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="name"
          label="工作空间名称"
          rules={[{ required: true, message: '请输入名称' }]}
        >
          <Input placeholder="如：Na2O 含量对烧结性能的影响研究" maxLength={256} />
        </Form.Item>
        <Form.Item
          name="question_text"
          label="研究问题"
          rules={[{ required: true, message: '请输入研究问题' }]}
        >
          <Input.TextArea
            placeholder="如：不同 Na2O 含量对烧结矿冶金性能有何影响？"
            maxLength={4096}
            autoSize={{ minRows: 2, maxRows: 6 }}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
