import { useEffect } from 'react';
import { Button, Form, Input, message, Typography } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { useAuthStore } from './AuthProvider';
import { OceanBackdrop } from '@/components/layout/OceanBackdrop';
import { OceanPanel } from '@/components/ui/OceanPanel';

const { Title } = Typography;

interface LoginFormValues {
  email: string;
  password: string;
}

/**
 * 登录页面 — Data Ocean Polar Mist 布局
 *
 * 55:45 桌面结构：左侧装饰视觉区（aria-hidden），右侧 OceanPanel 表单区。
 * 900px 以下隐藏装饰区，单列居中。
 * 保留：login、loading、认证重定向、requested redirect、表单验证、错误消息。
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
    <OceanBackdrop>
      <main className="ocean-login">
        <section className="ocean-login-visual" data-testid="login-visual" aria-hidden="true">
          <span className="ocean-index">IRIP / DATA OCEAN</span>
          <div className="ocean-login-hero">工业研究智能平台</div>
          <div className="ocean-login-track" />
        </section>
        <OceanPanel level="strong" className="ocean-login-panel">
          <Title level={1} style={{ marginBottom: 32 }}>
            IRIP 控制台
          </Title>
          <Form<LoginFormValues>
            aria-label="登录 IRIP"
            onFinish={handleSubmit}
            layout="vertical"
            autoComplete="on"
          >
            <Form.Item
              label="邮箱"
              name="email"
              rules={[
                { required: true, message: '请输入邮箱' },
                { type: 'email', message: '请输入有效的邮箱地址' },
              ]}
            >
              <Input placeholder="请输入邮箱" autoComplete="username" />
            </Form.Item>
            <Form.Item
              label="密码"
              name="password"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password placeholder="请输入密码" autoComplete="current-password" />
            </Form.Item>
            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" loading={loading} block>
                登录
              </Button>
            </Form.Item>
          </Form>
        </OceanPanel>
      </main>
    </OceanBackdrop>
  );
}
