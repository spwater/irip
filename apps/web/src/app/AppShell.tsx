import { useEffect } from 'react';
import { Avatar, Button, Layout, Menu, Space, Typography } from 'antd';
import { Outlet, useLocation, useNavigate } from '@tanstack/react-router';
import type { MenuProps } from 'antd';
import { useAuthStore } from '@/auth/AuthProvider';
import { JobDrawer, JobDrawerButton } from '@/jobs/JobDrawer';
import { OceanBackdrop } from '@/components/layout/OceanBackdrop';
import { ContentFrame } from '@/components/layout/ContentFrame';

const { Sider, Header, Content } = Layout;
const { Text } = Typography;

/** 导航菜单项（分组布局） */
const NAV_ITEMS: MenuProps['items'] = [
  { key: '/workbench', label: '研发看板' },
  { key: '/standards', label: '实验室建设' },
  { key: '/lab-ops', label: '实验室运营' },
  { key: '/platform', label: '平台应用' },
  { key: '/governance', label: '平台治理' },
];

/**
 * 主布局：Sider（导航菜单）+ Header（用户信息 + 登出）+ Content（Outlet）+ 全局 JobDrawer
 *
 * Data Ocean 升级：OceanBackdrop 包裹全局画布，导航区 aria-label="主导航"，
 * Outlet 放入 ContentFrame（wide 宽度）。
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
    <OceanBackdrop>
      <Layout className="ocean-shell">
        <Sider width={220} theme="light" breakpoint="lg" collapsedWidth={0}>
          <div className="ocean-shell-brand">IRIP</div>
          <nav aria-label="主导航">
            <Menu
              mode="inline"
              selectedKeys={[location.pathname]}
              items={NAV_ITEMS}
              onClick={handleMenuClick}
            />
          </nav>
        </Sider>
        <Layout>
          <Header className="ocean-shell-header">
            <Text className="ocean-shell-title">
              工业研究智能平台 Industrial Research Intelligence Platform
            </Text>
            <Space size="middle">
              <JobDrawerButton />
              <Space size="small">
                <Avatar size="small" className="ocean-shell-avatar">
                  {user.displayName.charAt(0)}
                </Avatar>
                <Text>{user.displayName}</Text>
              </Space>
              <Button type="link" onClick={handleLogout}>
                登出
              </Button>
            </Space>
          </Header>
          <Content className="ocean-shell-content" data-testid="ocean-app-content">
            <ContentFrame width="wide">
              <div className="ocean-enter">
                <Outlet />
              </div>
            </ContentFrame>
          </Content>
        </Layout>
        <JobDrawer />
      </Layout>
    </OceanBackdrop>
  );
}
