import { useEffect, useState } from 'react';
import { Button, Drawer, Form, Input, Typography, message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { apiUpdateAITool } from '@/api/models-ai';
import { extractApiError } from '@/api/types';
import type { AIToolDTO } from './types';

const { Text } = Typography;

type BuiltinToolEditDrawerProps = {
  open: boolean;
  tool: AIToolDTO | null;
  onClose: () => void;
};

/**
 * 内置工具编辑抽屉（编辑显示名+描述）
 */
export function BuiltinToolEditDrawer({
  open,
  tool,
  onClose,
}: BuiltinToolEditDrawerProps): JSX.Element {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<{ display_name: string; description: string }>();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (tool !== null) {
      form.setFieldsValue({
        display_name: tool.display_name,
        description: tool.description,
      });
    }
  }, [open, tool, form]);

  const handleSave = async (): Promise<void> => {
    if (!tool) return;
    try {
      const values = await form.validateFields();
      setSaving(true);
      await apiUpdateAITool(tool.name, {
        display_name: values.display_name,
        description: values.description,
        required_permission: tool.required_permission,
        parameters_schema: tool.parameters_schema,
        lock_version: tool.lock_version,
      });
      void queryClient.invalidateQueries({ queryKey: ['ai-tools'] });
      void queryClient.invalidateQueries({ queryKey: ['ingestion-tools'] });
      message.success('已保存');
      onClose();
    } catch (err: unknown) {
      if (err instanceof Error) {
        message.error(extractApiError(err));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer
      title="编辑内置工具"
      open={open}
      onClose={onClose}
      width={500}
      destroyOnClose
      extra={
        <Button
          type="primary"
          onClick={handleSave}
          loading={saving}
        >
          保存
        </Button>
      }
    >
      {tool && (
        <Form form={form} layout="vertical">
          <Form.Item label="工具名">
            <Text code>{tool.name}</Text>
          </Form.Item>
          <Form.Item
            name="display_name"
            label="显示名"
            rules={[
              { required: true, message: '请输入显示名' },
              { max: 128, message: '最长 128 字符' },
            ]}
          >
            <Input placeholder="如 XRD 解析器" />
          </Form.Item>
          <Form.Item
            name="description"
            label="描述"
            rules={[
              { required: true, message: '请输入工具描述' },
              { max: 2000, message: '最长 2000 字符' },
            ]}
          >
            <Input.TextArea rows={6} placeholder="工具描述" />
          </Form.Item>
        </Form>
      )}
    </Drawer>
  );
}
