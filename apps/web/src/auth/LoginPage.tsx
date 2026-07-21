import { useEffect } from 'react';
import { Button, Card, Form, Input, message, Typography } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { useAuthStore } from './AuthProvider';

const { Title } = Typography;

interface LoginFormValues {
  email: string;
  password: string;
}

/**
 * 登录页面
 * - Ant Design Form：邮箱 + 密码 + 登录按钮
 * - 中文标签
 * - 登录成功后跳回 requested route
 */
export function LoginPage(): JSX.Element {
  const login = useAuthStore((s) => s.login);
  const loading = useAuthStore((s) => s.loading);
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();

  // 读取 redirect 参数（登录成功后跳回的路径）
  const search = useSearch({ strict: false }) as Record<string, unknown>;
  const redirect = typeof search?.redirect === 'string' ? search.redirect : '/workbench';

  // 如果已认证，自动跳转（仅依赖 user，避免导航循环）
  useEffect(() => {
    if (user) {
      void navigate({ to: redirect });
    }
  }, [user]);

  const handleSubmit = async (values: LoginFormValues): Promise<void> => {
    const success = await login(values.email, values.password);
    if (success) {
      void navigate({ to: redirect });
    } else {
      void message.error('登录失败，请检查邮箱和密码');
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        background: '#f0f2f5',
      }}
    >
      <Card style={{ width: 400, boxShadow: '0 2px 8px rgba(0,0,0,0.09)' }}>
        <Title level={3} style={{ textAlign: 'center', marginBottom: 32 }}>
          IRIP 控制台
        </Title>
        <Form<LoginFormValues>
          onFinish={handleSubmit}
          layout="vertical"
          autoComplete="off"
        >
          <Form.Item
            label="邮箱"
            name="email"
            rules={[
              { required: true, message: '请输入邮箱' },
              { type: 'email', message: '请输入有效的邮箱地址' },
            ]}
          >
            <Input placeholder="请输入邮箱" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password placeholder="请输入密码" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button
              type="primary"
              htmlType="submit"
              loading={loading}
              block
            >
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
