import { useState, useEffect } from 'react';
import {
  Button,
  Card,
  Form,
  Input,
  Switch,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { http, extractApiError } from '@/api/client';

const { Title, Paragraph, Text } = Typography;

/** AI 配置类型 */
type AIConfig = {
  base_url: string;
  api_key_masked: string;
  model_name: string;
  enabled: boolean;
  thinking_enabled: boolean;
  updated_at: string | null;
};

/** 测试连接响应 */
type AITestResult = {
  success: boolean;
  message: string;
  model_response: string | null;
};

/**
 * AI 大模型配置页面
 *
 * 配置 OpenAI 兼容的 API 地址、密钥、模型名称。
 * 配置后 AI 助手自动使用真实模型，未配置时使用离线模拟模式。
 */
export function AIConfigPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const [testLoading, setTestLoading] = useState(false);

  const { data: config } = useQuery({
    queryKey: ['ai-config'],
    queryFn: async () => {
      const res = await http.get<AIConfig>('/ai-config');
      return res.data;
    },
  });

  useEffect(() => {
    if (config) {
      form.setFieldsValue({
        base_url: config.base_url,
        api_key: '',
        model_name: config.model_name,
        enabled: config.enabled,
        thinking_enabled: config.thinking_enabled,
      });
    }
  }, [config, form]);

  const saveMutation = useMutation({
    mutationFn: async (values: {
      base_url: string;
      api_key: string;
      model_name: string;
      enabled: boolean;
      thinking_enabled: boolean;
    }) => {
      const res = await http.put<AIConfig>('/ai-config', values);
      return res.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ai-config'] });
      message.success('AI 配置保存成功');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const handleTest = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      setTestLoading(true);
      const res = await http.post<AITestResult>('/ai-config/test', {
        base_url: values.base_url,
        api_key: values.api_key,
        model_name: values.model_name,
      });
      if (res.data.success) {
        message.success(`连接成功！模型回复: ${res.data.model_response ?? 'OK'}`);
      } else {
        message.error(`连接失败: ${res.data.message}`);
      }
    } catch (err: unknown) {
      message.error(extractApiError(err));
    } finally {
      setTestLoading(false);
    }
  };

  const handleSave = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      saveMutation.mutate({
        base_url: values.base_url,
        api_key: values.api_key,
        model_name: values.model_name,
        enabled: values.enabled ?? false,
        thinking_enabled: values.thinking_enabled ?? false,
      });
    } catch {
      // 校验失败
    }
  };

  return (
    <div>
      <Title level={5}>大模型配置</Title>
      <Paragraph type="secondary">
        配置 OpenAI 兼容的 API 地址和密钥。配置启用后，AI 助手将使用真实模型进行对话。
        未配置或未启用时，AI 助手使用离线模拟模式。
      </Paragraph>

      {config && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space size="large">
            <Text>当前状态: </Text>
            <Tag color={config.enabled ? 'green' : 'default'}>
              {config.enabled ? '已启用' : '未启用'}
            </Tag>
            {config.model_name && (
              <>
                <Text type="secondary">模型: {config.model_name}</Text>
                <Text type="secondary">密钥: {config.api_key_masked || '-'}</Text>
              </>
            )}
          </Space>
        </Card>
      )}

      <Form form={form} layout="vertical" style={{ maxWidth: 600 }}>
        <Form.Item
          name="base_url"
          label="API 地址"
          rules={[{ required: true, message: '请输入 API 地址' }]}
        >
          <Input placeholder="https://api.openai.com/v1" />
        </Form.Item>
        <Form.Item
          name="api_key"
          label="API 密钥"
          rules={[{ required: true, message: '请输入 API 密钥' }]}
        >
          <Input.Password placeholder="sk-..." />
        </Form.Item>
        <Form.Item
          name="model_name"
          label="模型名称"
          rules={[{ required: true, message: '请输入模型名称' }]}
        >
          <Input placeholder="gpt-4o / qwen-plus / deepseek-chat" />
        </Form.Item>
        <Form.Item name="enabled" label="启用" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item
          name="thinking_enabled"
          label="思考模式"
          valuePropName="checked"
          tooltip="启用后，请求中会携带 enable_thinking=true 参数（适用于 Qwen3 等支持思考模式的模型）"
        >
          <Switch />
        </Form.Item>
        <Form.Item>
          <Space>
            <Button type="primary" onClick={handleSave} loading={saveMutation.isPending}>
              保存配置
            </Button>
            <Button onClick={handleTest} loading={testLoading}>
              测试连接
            </Button>
          </Space>
        </Form.Item>
      </Form>
    </div>
  );
}
