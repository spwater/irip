import { useEffect } from 'react';
import { Button, Card, Form, Input, message, Typography } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { useAuthStore } from './AuthProvider';
import { OceanBackdrop } from '@/components/layout/OceanBackdrop';

const { Title, Text } = Typography;

interface LoginFormValues {
  email: string;
  password: string;
}

/**
 * 登录页面 — 入口页原型（设计文档第 10.1 节）
 *
 * - 桌面约 55:45 双区布局：左侧数据关系品牌，右侧登录 surface
 * - 表单字段、校验、loading、错误 message、认证成功重定向保持不变
 * - 小于 900px 改单列，隐藏左侧视觉区
 * - 不预填演示账号
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
    <>
      {/* 全局极地雾蓝背景 */}
      <OceanBackdrop />

      <div
        style={{
          position: 'relative',
          zIndex: 10,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'stretch',
        }}
      >
        {/* 左侧：数据关系品牌区（55%，< 900px 隐藏） */}
        <div
          style={{
            flex: '55',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            padding: '64px 56px',
            position: 'relative',
          }}
          className="ocean-login-brand"
        >
          {/* 品牌标识 */}
          <div style={{ marginBottom: 48 }}>
            <Text
              style={{
                fontSize: 11,
                letterSpacing: 3,
                textTransform: 'uppercase',
                color: 'var(--ocean-text-muted)',
                fontFamily: 'var(--ocean-font-mono)',
              }}
            >
              Industrial Research Intelligence Platform
            </Text>
            <h1
              style={{
                margin: '8px 0 0',
                fontSize: 48,
                fontWeight: 650,
                lineHeight: 1.1,
                color: 'var(--ocean-text-primary)',
                letterSpacing: 1,
              }}
            >
              IRIP
            </h1>
            <Text
              style={{
                display: 'block',
                marginTop: 12,
                fontSize: 18,
                color: 'var(--ocean-text-secondary)',
                lineHeight: 1.6,
                maxWidth: 420,
              }}
            >
              数据之海 · 工业研究智能平台
            </Text>
          </div>

          {/* 能力索引 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {[
              { idx: '01', label: '实验数据采集与事实管理' },
              { idx: '02', label: '流程编排与自动执行' },
              { idx: '03', label: '模型版本与预测工作台' },
              { idx: '04', label: 'AI 助手与数据溯源' },
            ].map((item) => (
              <div key={item.idx} style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
                <span
                  style={{
                    fontSize: 11,
                    letterSpacing: 2,
                    color: 'var(--ocean-action-primary)',
                    fontFamily: 'var(--ocean-font-mono)',
                    flex: '0 0 auto',
                  }}
                >
                  {item.idx}
                </span>
                <Text
                  style={{
                    fontSize: 15,
                    color: 'var(--ocean-text-secondary)',
                  }}
                >
                  {item.label}
                </Text>
              </div>
            ))}
          </div>
        </div>

        {/* 右侧：登录 surface（45%） */}
        <div
          style={{
            flex: '45',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 24,
          }}
          className="ocean-login-form"
        >
          <Card
            style={{
              width: '100%',
              maxWidth: 400,
              background: 'var(--ocean-surface-strong)',
              border: '1px solid var(--ocean-border-subtle)',
              borderRadius: 8,
              boxShadow: 'var(--ocean-shadow-panel)',
              backdropFilter: 'blur(4px)',
            }}
          >
            <Title
              level={3}
              style={{
                textAlign: 'center',
                marginBottom: 8,
                color: 'var(--ocean-text-primary)',
                fontWeight: 650,
              }}
            >
              IRIP 控制台
            </Title>
            <Text
              type="secondary"
              style={{
                display: 'block',
                textAlign: 'center',
                marginBottom: 28,
                fontSize: 13,
              }}
            >
              请使用工作邮箱登录
            </Text>
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
      </div>

      {/* 响应式：< 900px 改单列，隐藏左侧品牌区 */}
      <style>{`
        @media (max-width: 900px) {
          .ocean-login-brand {
            display: none !important;
          }
          .ocean-login-form {
            flex: 1 !important;
            padding: 16px !important;
          }
        }
      `}</style>
    </>
  );
}
