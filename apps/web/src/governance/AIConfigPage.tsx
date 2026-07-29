import { useState, useEffect } from 'react';
import {
  Button,
  Form,
  Input,
  Switch,
  Space,
  Typography,
  message,
} from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { http } from '@/api/client';
import { extractApiError } from '@/api/types';
import { OceanPanel, StatusMark } from '@/components/ui';

const { Title, Paragraph, Text } = Typography;

/** AI 配置类型 */
type AIConfig = {
  base_url: string;
  api_key_masked: string;
  model_name: string;
  enabled: boolean;
  meta_prompt: string | null;
  updated_at: string | null;
};

/** 测试连接响应 */
type AITestResult = {
  success: boolean;
  message: string;
  model_response: string | null;
};

/**
 * AI 大模型配置页面（设计文档第 10.7 节 — 治理监控原型）
 */
export function AIConfigPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const [promptForm] = Form.useForm();
  const [testLoading, setTestLoading] = useState(false);

  const { data: config } = useQuery({
    queryKey: ['ai-config'],
    queryFn: async () => {
      const res = await http.get<AIConfig>('/ai-config');
      return res.data;
    },
  });

  // 大模型配置表单回填
  useEffect(() => {
    if (config) {
      form.setFieldsValue({
        base_url: config.base_url,
        api_key: '',
        model_name: config.model_name,
        enabled: config.enabled,
      });
    }
  }, [config, form]);

  // 提示词表单回填（预加载当前已保存的提示词）
  useEffect(() => {
    if (config) {
      promptForm.setFieldsValue({
        meta_prompt: config.meta_prompt || '',
      });
    }
  }, [config, promptForm]);

  // ---- 保存大模型配置 ----
  const saveMutation = useMutation({
    mutationFn: async (values: {
      base_url: string;
      api_key: string;
      model_name: string;
      enabled: boolean;
      meta_prompt: string;
    }) => {
      const res = await http.put<AIConfig>('/ai-config', values);
      return res.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ai-config'] });
      message.success('大模型配置保存成功');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 单独保存提示词 ----
  const savePromptMutation = useMutation({
    mutationFn: async (meta_prompt: string) => {
      const res = await http.put<{ meta_prompt: string | null }>('/ai-config/meta-prompt', { meta_prompt });
      return res.data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ai-config'] });
      message.success('提示词保存成功');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const handleTest = async (): Promise<void> => {
    if (!config || !config.base_url || !config.model_name) {
      message.warning('请先保存配置后再测试连接');
      return;
    }
    try {
      setTestLoading(true);
      const res = await http.post<AITestResult>('/ai-config/test', {
        base_url: config.base_url,
        api_key: config.api_key_masked ? '__use_saved__' : '',
        model_name: config.model_name,
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

  const handleSaveModel = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      // 保留已有的 meta_prompt
      const existingPrompt = config?.meta_prompt || '';
      saveMutation.mutate({
        base_url: values.base_url,
        api_key: values.api_key,
        model_name: values.model_name,
        enabled: values.enabled ?? false,
        meta_prompt: existingPrompt,
      });
    } catch {
      // 校验失败
    }
  };

  const handleSavePrompt = async (): Promise<void> => {
    try {
      const values = await promptForm.validateFields();
      savePromptMutation.mutate(values.meta_prompt || '');
    } catch {
      // 校验失败
    }
  };

  return (
    <div>
      <Title level={5} style={{ color: 'var(--ocean-text-primary)' }}>大模型配置</Title>
      <Paragraph type="secondary">
        配置 OpenAI 兼容的 API 地址和密钥。配置启用后，小艾将使用真实模型进行对话。
        未配置或未启用时，小艾使用离线模拟模式。
      </Paragraph>

      {config && (
        <OceanPanel variant="default" padding="12px 16px" style={{ marginBottom: 16 }}>
          <Space size="large" align="center">
            <Button size="small" onClick={handleTest} loading={testLoading}>
              测试连接
            </Button>
            <Text>当前状态: </Text>
            <StatusMark
              semantic={config.enabled ? 'success' : 'neutral'}
              label={config.enabled ? '已启用' : '未启用'}
            />
            {config.model_name && (
              <>
                <Text type="secondary">模型: {config.model_name}</Text>
                <Text type="secondary">密钥: {config.api_key_masked || '-'}</Text>
              </>
            )}
          </Space>
        </OceanPanel>
      )}

      {/* ---- 大模型配置区 ---- */}
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
        <Form.Item label="启用">
          <Space>
            <Form.Item name="enabled" valuePropName="checked" noStyle>
              <Switch />
            </Form.Item>
            <Button type="primary" onClick={handleSaveModel} loading={saveMutation.isPending}>
              保存大模型配置
            </Button>
          </Space>
        </Form.Item>
      </Form>

      {/* ---- 提示词推荐系统提示词区 ---- */}
      <div style={{ marginTop: 24, maxWidth: 800 }}>
        <Title level={5} style={{ color: 'var(--ocean-text-primary)' }}>数据接口推荐-系统提示词</Title>
        <Paragraph type="secondary" style={{ fontSize: 13 }}>
          用于「提示词推荐」功能的大模型系统提示词。留空则使用内置默认版本。可用 {'{body.filename}'} 作为文件名占位符。
        </Paragraph>
        <Form form={promptForm} layout="vertical">
          <Form.Item name="meta_prompt">
            <Input.TextArea
              rows={12}
              placeholder="留空使用内置默认提示词。可用 {body.filename} 作为文件名占位符。"
              style={{ fontFamily: 'var(--ocean-font-mono)', fontSize: 13 }}
            />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={handleSavePrompt} loading={savePromptMutation.isPending}>
              保存提示词
            </Button>
          </Form.Item>
        </Form>
      </div>
    </div>
  );
}
