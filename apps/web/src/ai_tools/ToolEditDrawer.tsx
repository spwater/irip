import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Radio,
  Space,
  Typography,
  message,
} from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiCreateAITool, apiUpdateAITool } from '@/api/models-ai';
import { extractApiError } from '@/api/types';
import type { AIToolDTO } from './types';

const { Text } = Typography;

type ToolEditDrawerProps = {
  /** 抽屉是否打开 */
  open: boolean;
  /** 编辑的工具；null 表示新建模式 */
  tool: AIToolDTO | null;
  /** 关闭回调 */
  onClose: () => void;
};

type FormValues = {
  name: string;
  display_name: string;
  description: string;
  required_permission: string;
  candidate: boolean;
};

/**
 * AI 工具编辑/新建抽屉
 *
 * - 新建模式（tool=null）：name 可填 + 黄色 Alert 提示"仅创建声明层"；
 * - 编辑模式（tool≠null）：name 只读，其余字段可改；
 * - parameters_schema 用 TextArea + monospace，JSON.parse 实时校验；
 * - candidate 切换为"候选"时弹 Modal 二次确认（U-5）；
 * - 保存调对应 API，成功后 invalidate ['ai-tools'] + toast。
 */
export function ToolEditDrawer({
  open,
  tool,
  onClose,
}: ToolEditDrawerProps): JSX.Element {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<FormValues>();
  const [schemaText, setSchemaText] = useState<string>('{}');
  const [schemaError, setSchemaError] = useState<string | null>(null);

  const isCreate = tool === null;

  useEffect(() => {
    if (!open) return;
    if (tool !== null) {
      form.setFieldsValue({
        name: tool.name,
        display_name: tool.display_name,
        description: tool.description,
        required_permission: tool.required_permission,
        candidate: tool.candidate,
      });
      setSchemaText(
        JSON.stringify(tool.parameters_schema, null, 2) ?? '{}',
      );
    } else {
      form.resetFields();
      form.setFieldsValue({ candidate: false });
      setSchemaText('{}');
    }
    setSchemaError(null);
  }, [open, tool, form]);

  const validateSchema = (text: string): boolean => {
    try {
      JSON.parse(text);
      setSchemaError(null);
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setSchemaError(msg);
      return false;
    }
  };

  const handleSchemaChange = (text: string): void => {
    setSchemaText(text);
    validateSchema(text);
  };

  const saveMutation = useMutation({
    mutationFn: async (values: FormValues): Promise<AIToolDTO> => {
      let schema: Record<string, unknown>;
      try {
        schema = JSON.parse(schemaText);
      } catch {
        throw new Error('参数 Schema 不是合法 JSON');
      }
      if (isCreate) {
        return apiCreateAITool({
          name: values.name,
          display_name: values.display_name,
          description: values.description,
          required_permission: values.required_permission,
          candidate: values.candidate,
          parameters_schema: schema,
        });
      }
      const currentTool = tool as AIToolDTO;
      return apiUpdateAITool(currentTool.name, {
        display_name: values.display_name,
        description: values.description,
        required_permission: values.required_permission,
        candidate: values.candidate,
        parameters_schema: schema,
        lock_version: currentTool.lock_version,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ai-tools'] });
      void queryClient.invalidateQueries({ queryKey: ['assistant-provider-status'] });
      message.success(
        isCreate
          ? '工具创建成功，下次小艾对话即生效'
          : '工具已保存，下次小艾对话即生效',
      );
      onClose();
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const handleCandidateChange = (value: boolean): void => {
    if (value && !isCreate && tool !== null && !tool.candidate) {
      Modal.confirm({
        title: '切换为候选工具',
        content:
          '将工具切换为"候选"后，AI 不会自动执行此工具，仅返回建议供人工审批。确认切换？',
        okText: '确认切换',
        cancelText: '取消',
        onOk: () => {
          form.setFieldsValue({ candidate: true });
        },
        onCancel: () => {
          form.setFieldsValue({ candidate: false });
        },
      });
    } else {
      form.setFieldsValue({ candidate: value });
    }
  };

  const handleSave = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      if (!validateSchema(schemaText)) {
        message.error('参数 Schema 不是合法 JSON，请修正后再保存');
        return;
      }
      saveMutation.mutate(values);
    } catch {
      // 表单校验失败，antd 自动展示字段错误
    }
  };

  return (
    <Drawer
      title={isCreate ? '新建工具' : '编辑工具'}
      open={open}
      onClose={onClose}
      width={600}
      destroyOnClose
      extra={
        <Button
          type="primary"
          onClick={handleSave}
          loading={saveMutation.isPending}
          disabled={schemaError !== null}
        >
          保存
        </Button>
      }
    >
      {isCreate && (
        <Alert
          type="warning"
          message="仅创建声明层"
          description={
            '新建工具仅创建工具声明（名称、描述、参数 Schema），不会自动实现执行逻辑。AI 调用未实现的工具将返回"未实现"提示。'
          }
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      <Form form={form} layout="vertical">
        <Form.Item
          name="name"
          label="工具名"
          rules={[
            {
              required: true,
              message: '请输入工具名',
            },
            {
              pattern: /^[a-z][a-z0-9_]*$/,
              message: '小写字母开头，仅含小写字母/数字/下划线',
            },
            { max: 64, message: '最长 64 字符' },
          ]}
        >
          <Input
            placeholder="如 search_standards"
            disabled={!isCreate}
          />
        </Form.Item>

        <Form.Item
          name="display_name"
          label="显示名"
          rules={[
            { required: true, message: '请输入显示名' },
            { max: 128, message: '最长 128 字符' },
          ]}
        >
          <Input placeholder="如 搜索标准变量" />
        </Form.Item>

        <Form.Item
          name="description"
          label="描述"
          rules={[
            { required: true, message: '请输入工具描述' },
            { max: 2000, message: '最长 2000 字符' },
          ]}
        >
          <Input.TextArea rows={3} placeholder="工具描述，供 AI 理解工具用途" />
        </Form.Item>

        <Form.Item
          name="required_permission"
          label="所需权限"
          rules={[
            { required: true, message: '请输入所需权限' },
            { max: 64, message: '最长 64 字符' },
          ]}
        >
          <Input placeholder="如 standard:read" />
        </Form.Item>

        <Form.Item
          name="candidate"
          label="类型"
          rules={[{ required: true, message: '请选择工具类型' }]}
        >
          <Radio.Group
            onChange={(e) => handleCandidateChange(e.target.value)}
          >
            <Radio value={false}>只读（白名单，可直接执行）</Radio>
            <Radio value={true}>候选（需人工审批）</Radio>
          </Radio.Group>
        </Form.Item>

        <Form.Item
          label="参数 Schema (JSON)"
          required
          help={
            schemaError ? (
              <Text type="danger">{schemaError}</Text>
            ) : (
              <Text type="success">合法 JSON</Text>
            )
          }
        >
          <Input.TextArea
            value={schemaText}
            onChange={(e) => handleSchemaChange(e.target.value)}
            rows={10}
            style={{ fontFamily: 'monospace' }}
            placeholder='{"type": "object", "properties": {...}}'
          />
        </Form.Item>

        {!isCreate && tool !== null && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text type="secondary">
              乐观锁版本: {tool.lock_version}（保存时自动校验，冲突需刷新后重试）
            </Text>
            <Text type="secondary">
              启用状态: {tool.enabled ? '已启用' : '已禁用'}
            </Text>
          </Space>
        )}
      </Form>
    </Drawer>
  );
}
