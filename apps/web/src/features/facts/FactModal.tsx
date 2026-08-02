/**
 * 实验数据创建/编辑表单 Modal（阶段2新增）
 *
 * 集成：
 * - DepartmentSelector（归属部门选择器）
 * - PublishPrivateToggle（发布为私有勾选框）
 * - 提交时 department_id 必填，visibility_scope 随勾选设 private/tree，
 *   owner_user_id 自动填当前用户
 */
import { useState } from 'react';
import { Form, Input, Modal, Select, message } from 'antd';
import { DepartmentSelector } from '@/shared/DepartmentSelector';
import { PublishPrivateToggle } from '@/shared/PublishPrivateToggle';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { http } from '@/api/client';

/** FactModal 组件 Props */
export type FactModalProps = {
  /** 是否打开 */
  open: boolean;
  /** 关闭回调 */
  onClose: () => void;
  /** 创建/编辑成功回调 */
  onSuccess?: () => void;
  /** 编辑时的 fact ID（不传为新建） */
  factId?: string;
};

/**
 * 实验数据创建/编辑表单 Modal
 *
 * 阶段2多租户隔离键升级：集成部门选择器 + 私有发布勾选。
 */
export function FactModal({ open, onClose, onSuccess, factId }: FactModalProps): JSX.Element {
  const [form] = Form.useForm();
  const [isPrivate, setIsPrivate] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const user = useAuthStore((s) => s.user);

  const handleSubmit = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);

      const payload: Record<string, unknown> = {
        ...values,
        visibility_scope: isPrivate ? 'private' : 'tree',
        owner_user_id: user?.user_id,
      };

      if (factId) {
        await http.patch(`/facts/${factId}`, payload);
        message.success('事实更新成功');
      } else {
        await http.post('/facts', payload);
        message.success('事实创建成功');
      }

      form.resetFields();
      setIsPrivate(false);
      onSuccess?.();
      onClose();
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'response' in err) {
        const response = (err as { response?: { data?: { error?: { message?: string } } } }).response;
        if (response?.data?.error?.message) {
          message.error(response.data.error.message);
          return;
        }
      }
      if (err instanceof Error && err.message) {
        message.error(err.message);
        return;
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={factId ? '编辑实验数据' : '新建实验数据'}
      open={open}
      onCancel={() => {
        form.resetFields();
        setIsPrivate(false);
        onClose();
      }}
      onOk={handleSubmit}
      confirmLoading={submitting}
      okText={factId ? '保存' : '创建'}
      cancelText="取消"
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="department_id"
          label="归属部门"
          rules={[{ required: true, message: '请选择归属部门' }]}
        >
          <DepartmentSelector
            allowRoot={(user?.roles ?? []).includes('platform_administrator')}
            placeholder="选择归属部门"
          />
        </Form.Item>

        <Form.Item
          name="fact_type"
          label="事实类型"
          rules={[{ required: true, message: '请选择事实类型' }]}
        >
          <Select
            placeholder="选择事实类型"
            options={[
              { value: 'experiment_run', label: '实验运行' },
              { value: 'simulation_run', label: '仿真运行' },
              { value: 'document_record', label: '文档记录' },
              { value: 'model_execution', label: '模型执行' },
            ]}
          />
        </Form.Item>

        <Form.Item
          name="subject_id"
          label="样品标识"
          rules={[{ required: true, message: '请输入样品标识' }]}
        >
          <Input placeholder="如：样品编号、批次号" />
        </Form.Item>

        <Form.Item
          name="description"
          label="描述"
        >
          <Input.TextArea
            placeholder="数据描述（可选）"
            rows={2}
          />
        </Form.Item>

        {/* 私有发布勾选 */}
        <Form.Item label="可见性设置">
          <PublishPrivateToggle
            checked={isPrivate}
            onChange={setIsPrivate}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
