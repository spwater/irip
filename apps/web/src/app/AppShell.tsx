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

/** 导航菜单项（一级入口，文案与跳转保持不变） */
const NAV_ITEMS: MenuProps['items'] = [
  { key: '/workbench', label: '研发看板' },
  { key: '/standards', label: '实验室建设' },
  { key: '/lab-ops', label: '实验室运营' },
  { key: '/platform', label: '平台应用' },
  { key: '/governance', label: '平台治理' },
];

/**
 * 主布局：
 * OceanBackdrop 包裹 → AppShell
 *   ├─ Sider（浅雾蓝结构层，选中项中蓝光带 + 左侧细线）
 *   ├─ Layout
 *   │  ├─ Header（降低视觉重量，保留作业、用户、登出）
 *   │  └─ Content（ContentFrame → Outlet）
 *   └─ JobDrawer
 *
 * 认证重定向、菜单点击、用户信息、登出和 JobDrawer 行为保持不变。
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

  // 详情路由进入时，所属一级模块导航保持视觉选中（前缀映射）
  const matchedItem = NAV_ITEMS?.find((item) =>
    item && 'key' in item && typeof item.key === 'string'
      ? location.pathname.startsWith(item.key)
      : false,
  );
  const selectedKey: string =
    matchedItem && 'key' in matchedItem && typeof matchedItem.key === 'string'
      ? matchedItem.key
      : location.pathname;

  return (
    <>
      {/* 全局极地雾蓝背景：固定定位，z-index 0，不承载业务数据 */}
      <OceanBackdrop />

      <Layout style={{ minHeight: '100vh', background: 'transparent', position: 'relative', zIndex: 10 }}>
        {/* 导航：浅雾蓝结构层，选中项中蓝光带 + 左侧细线 + 文字增强 */}
        <Sider
          width={212}
          breakpoint="lg"
          collapsedWidth={0}
          theme="light"
          style={{
            background: 'var(--ocean-surface-structural)',
            borderRight: '1px solid var(--ocean-border-subtle)',
            backdropFilter: 'none',
          }}
        >
          {/* IRIP 品牌索引 */}
          <div
            style={{
              height: 56,
              display: 'flex',
              alignItems: 'center',
              padding: '0 20px',
              gap: 10,
              borderBottom: '1px solid var(--ocean-border-subtle)',
            }}
          >
            <span
              style={{
                fontSize: 20,
                fontWeight: 700,
                letterSpacing: 1,
                color: 'var(--ocean-action-primary)',
              }}
            >
              IRIP
            </span>
            <span
              style={{
                fontSize: 10,
                letterSpacing: 2,
                textTransform: 'uppercase',
                color: 'var(--ocean-text-muted)',
                fontFamily: 'var(--ocean-font-mono)',
              }}
            >
              Data Ocean
            </span>
          </div>
          <Menu
            mode="inline"
            selectedKeys={[selectedKey]}
            items={NAV_ITEMS}
            onClick={handleMenuClick}
            style={{
              background: 'transparent',
              borderInlineEnd: 'none',
              padding: '8px 12px',
            }}
          />
        </Sider>

        <Layout style={{ background: 'transparent' }}>
          {/* 顶栏：降低视觉重量，透明背景 + 底部分隔线 */}
          <Header
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              background: 'rgba(232, 246, 249, 0.5)',
              backdropFilter: 'blur(6px)',
              padding: '0 24px',
              borderBottom: '1px solid var(--ocean-border-subtle)',
              position: 'sticky',
              top: 0,
              zIndex: 100,
            }}
          >
            <Text
              style={{
                fontSize: 15,
                fontWeight: 600,
                color: 'var(--ocean-text-primary)',
                letterSpacing: 0.3,
              }}
            >
              工业研究智能平台
              <span
                style={{
                  marginLeft: 10,
                  fontSize: 11,
                  letterSpacing: 1.5,
                  color: 'var(--ocean-text-muted)',
                  fontFamily: 'var(--ocean-font-mono)',
                  textTransform: 'uppercase',
                }}
              >
                Industrial Research Intelligence Platform
              </span>
            </Text>
            <Space size="middle">
              <JobDrawerButton />
              <Space size="small" align="center">
                <Avatar
                  size="small"
                  style={{
                    backgroundColor: 'var(--ocean-action-primary)',
                    color: '#FFFFFF',
                  }}
                >
                  {user.displayName.charAt(0)}
                </Avatar>
                <Text style={{ color: 'var(--ocean-text-primary)' }}>
                  {user.displayName}
                </Text>
              </Space>
              <Button type="link" onClick={handleLogout}>
                登出
              </Button>
            </Space>
          </Header>

          {/* 内容区：统一内容框架，移除 #f0f2f5 硬编码 */}
          <Content style={{ background: 'transparent', padding: '20px 0 0' }}>
            <ContentFrame>
              <Outlet />
            </ContentFrame>
          </Content>
        </Layout>
      </Layout>

      {/* 全局作业抽屉 */}
      <JobDrawer />
    </>
  );
}
