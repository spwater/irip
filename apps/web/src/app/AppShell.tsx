import { useEffect } from 'react';
import { Avatar, Button, Layout, Menu, Space, Typography } from 'antd';
import { Outlet, useLocation, useNavigate } from '@tanstack/react-router';
import type { MenuProps } from 'antd';
import { useAuthStore } from '@/auth/AuthProvider';
import { JobDrawer, JobDrawerButton } from '@/jobs/JobDrawer';

const { Sider, Header, Content } = Layout;
const { Text } = Typography;

/** 导航菜单项（中文标签） */
const NAV_ITEMS: MenuProps['items'] = [
  { key: '/workbench', label: '研发看板' },
  { key: '/standards', label: '要素管理' },
  { key: '/facts', label: '实验事实' },
  { key: '/parameters', label: '参数管理' },
  { key: '/components', label: '组件管理' },
  { key: '/flows', label: '流程编排' },
  { key: '/models', label: '模型管理' },
  { key: '/assistant', label: 'AI 助手' },
  { key: '/governance', label: '平台治理' },
  { key: '/jobs', label: '作业中心' },
];

/**
 * 主布局：Sider（导航菜单）+ Header（用户信息 + 登出）+ Content（Outlet）+ 全局 JobDrawer
 */
export function AppShell(): JSX.Element | null {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const location = useLocation();

  // 未认证时重定向到登录页（仅依赖 user，避免导航循环）
  useEffect(() => {
    if (!user) {
      void navigate({ to: '/login', search: { redirect: location.pathname } });
    }
  }, [user]);

  if (!user) return null;

  const handleLogout = async (): Promise<void> => {
    await logout();
    void navigate({ to: '/login' });
  };

  const handleMenuClick = ({ key }: { key: string }): void => {
    void navigate({ to: key });
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={220} theme="light" breakpoint="lg" collapsedWidth={0}>
        <div
          style={{
            height: 48,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontWeight: 700,
            fontSize: 18,
            color: '#1677ff',
          }}
        >
          IRIP
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={NAV_ITEMS}
          onClick={handleMenuClick}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            background: '#fff',
            padding: '0 24px',
            borderBottom: '1px solid #f0f0f0',
          }}
        >
          <Text strong>IRIP 控制台</Text>
          <Space size="middle">
            <JobDrawerButton />
            <Space size="small">
              <Avatar size="small" style={{ backgroundColor: '#1677ff' }}>
                {user.displayName.charAt(0)}
              </Avatar>
              <Text>{user.displayName}</Text>
            </Space>
            <Button type="link" onClick={handleLogout}>
              登出
            </Button>
          </Space>
        </Header>
        <Content style={{ padding: 24, background: '#f0f2f5' }}>
          <Outlet />
        </Content>
      </Layout>
      <JobDrawer />
    </Layout>
  );
}
