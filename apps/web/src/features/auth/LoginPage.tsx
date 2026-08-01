import { useEffect } from 'react';
import { Button, Card, Form, Input, message, Typography } from 'antd';
import { MailOutlined, LockOutlined } from '@ant-design/icons';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { useAuthStore } from './AuthProvider';
import { OceanBackdrop } from '@/shared/layout/OceanBackdrop';

const { Title, Text } = Typography;

interface LoginFormValues {
  email: string;
  password: string;
}

/** 平台能力索引（登录页左侧品牌区） */
const CAPABILITIES: ReadonlyArray<{ idx: string; label: string }> = [
  { idx: '01', label: '实验数据采集与事实管理' },
  { idx: '02', label: '流程编排与自动执行' },
  { idx: '03', label: '模型版本与预测工作台' },
  { idx: '04', label: 'AI 助手与数据溯源' },
];

/**
 * 登录页面「潮线 Tideline · 水光版」
 *
 * - 左侧：DATA/OCEAN 双水印 + 渐变文字大标题 + 波形线 + 编号能力索引
 * - 右侧：圆角玻璃登录卡（顶部渐变光边）
 * - 表单字段、校验、loading、错误 message、认证成功重定向保持不变
 * - 小于 900px 改单列，隐藏左侧视觉区
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
      {/* 全局潮汐背景 */}
      <OceanBackdrop />

      <div
        style={{
          position: 'relative',
          zIndex: 10,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'stretch',
          overflow: 'hidden',
        }}
      >
        {/* 左侧：品牌视觉区（55%，< 900px 隐藏） */}
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
          {/* 超大水印：DATA OCEAN */}
          <span
            className="ocean-watermark ocean-watermark--solid"
            aria-hidden="true"
            style={{
              left: -24,
              top: '8%',
              fontSize: 'clamp(120px, 12.5vw, 240px)',
            }}
          >
            Data
          </span>
          <span
            className="ocean-watermark"
            aria-hidden="true"
            style={{
              left: 40,
              top: 'calc(8% + clamp(110px, 11vw, 220px))',
              fontSize: 'clamp(120px, 12.5vw, 240px)',
            }}
          >
            Ocean
          </span>

          {/* 品牌区：渐变文字 + 波形线（无底色块） */}
          <div
            className="ocean-tide-enter"
            style={{ position: 'relative', maxWidth: 520 }}
          >
            <Text
              style={{
                fontSize: 10,
                letterSpacing: 3,
                textTransform: 'uppercase',
                color: 'var(--ocean-current-bright)',
                fontFamily: 'var(--ocean-font-mono)',
                fontWeight: 600,
              }}
            >
              Industrial Research Intelligence Platform
            </Text>
            <h1
              className="ocean-flow-text"
              style={{
                margin: '16px 0 0',
                fontSize: 88,
                fontWeight: 800,
                lineHeight: 0.98,
                letterSpacing: 4,
              }}
            >
              IRIP
            </h1>
            <Text
              style={{
                display: 'block',
                marginTop: 18,
                fontSize: 19,
                color: 'var(--ocean-text-secondary)',
                lineHeight: 1.6,
                letterSpacing: 1,
              }}
            >
              数据之海 · 工业研究智能平台
            </Text>
            {/* 装饰坐标行 */}
            <Text
              style={{
                display: 'block',
                marginTop: 16,
                fontSize: 9,
                letterSpacing: 2,
                color: 'var(--ocean-text-muted)',
                fontFamily: 'var(--ocean-font-mono)',
              }}
            >
              31.23°N 121.47°E · TIDE-07 · RESEARCH STATION
            </Text>
          </div>

          {/* 能力索引：编号 + 圆头小横 */}
          <div
            className="ocean-tide-enter ocean-tide-enter--d2"
            style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 48, position: 'relative' }}
          >
            {CAPABILITIES.map((item) => (
              <div key={item.idx} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span
                  style={{
                    fontSize: 11,
                    letterSpacing: 2,
                    color: 'var(--ocean-abyss-deep)',
                    fontFamily: 'var(--ocean-font-mono)',
                    fontWeight: 700,
                    flex: '0 0 auto',
                  }}
                >
                  {item.idx}
                </span>
                <span
                  aria-hidden="true"
                  style={{
                    width: 16,
                    height: 2,
                    borderRadius: 999,
                    background: 'linear-gradient(90deg, #0E5B84, #17B8CE)',
                    flex: '0 0 auto',
                  }}
                />
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
            className="ocean-login-card ocean-tide-enter ocean-tide-enter--d1"
            style={{
              width: '100%',
              maxWidth: 400,
              background: 'var(--ocean-surface-strong)',
              border: '1px solid var(--ocean-border-subtle)',
              borderRadius: 14,
              boxShadow: '0 24px 64px rgba(29, 78, 103, 0.18)',
              backdropFilter: 'blur(8px)',
            }}
          >
            <Title
              level={3}
              style={{
                textAlign: 'center',
                marginBottom: 8,
                color: '#0C4667',
                fontWeight: 800,
                letterSpacing: '0.08em',
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
                <Input prefix={<MailOutlined />} placeholder="请输入邮箱" />
              </Form.Item>
              <Form.Item
                label="密码"
                name="password"
                rules={[{ required: true, message: '请输入密码' }]}
              >
                <Input.Password prefix={<LockOutlined />} placeholder="请输入密码" />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={loading}
                  block
                  style={{
                    background: 'linear-gradient(115deg, #0E5B84 0%, #1296AC 60%, #17B8CE 100%)',
                    border: 'none',
                    borderRadius: 10,
                    height: 40,
                    fontWeight: 600,
                    letterSpacing: 4,
                    boxShadow: '0 8px 20px rgba(14, 91, 132, 0.28)',
                  }}
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
