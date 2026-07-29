import { useEffect } from 'react';
import { Avatar, Button, Layout, Menu, Space, Typography } from 'antd';
import { Outlet, useLocation, useNavigate } from '@tanstack/react-router';
import type { MenuProps } from 'antd';
import { useAuthStore } from '@/auth/AuthProvider';
import { JobDrawer, JobDrawerButton } from '@/jobs/JobDrawer';
import { OceanBackdrop } from '@/components/layout/OceanBackdrop';
import { ContentFrame } from '@/components/layout/ContentFrame';
import { PageHeaderProvider, usePageHeader } from './PageHeaderContext';

const { Sider, Header, Content } = Layout;
const { Title, Text } = Typography;

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
 *   ├─ Sider（固定定位，不随滚动）
 *   ├─ Layout
 *   │  ├─ Header（动态：显示当前页面的 index/title/tabs/actions）
 *   │  └─ Content（ContentFrame → Outlet）
 *   └─ JobDrawer
 */
export function AppShell(): JSX.Element | null {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const location = useLocation();

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
      <OceanBackdrop />
      <PageHeaderProvider>
        <Layout style={{ minHeight: '100vh', background: 'transparent', position: 'relative', zIndex: 10 }}>
          {/* 导航：固定定位，不随滚动 */}
          <Sider
            width={212}
            breakpoint="lg"
            collapsedWidth={0}
            theme="light"
            style={{
              background: 'var(--ocean-surface-structural)',
              borderRight: '1px solid var(--ocean-border-subtle)',
              position: 'fixed',
              left: 0,
              top: 0,
              bottom: 0,
              overflow: 'auto',
              zIndex: 200,
            }}
          >
            <div
              style={{
                minHeight: 88,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
                padding: '12px 20px',
                gap: 4,
                borderBottom: '1px solid var(--ocean-border-subtle)',
              }}
            >
              {/* 英文行：Data Ocean (左) + IRIP (右)，底部对齐 */}
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                <span
                  style={{
                    fontSize: 10,
                    letterSpacing: 2,
                    textTransform: 'uppercase',
                    color: 'var(--ocean-text-secondary)',
                    fontFamily: 'var(--ocean-font-mono)',
                    fontWeight: 600,
                    lineHeight: 1,
                  }}
                >
                  Data Ocean
                </span>
                <span
                  style={{
                    fontSize: 28,
                    fontWeight: 800,
                    letterSpacing: 1.5,
                    color: 'var(--ocean-action-primary)',
                    lineHeight: 1,
                  }}
                >
                  IRIP
                </span>
              </div>
              {/* 中文行：工业研究智能平台，与英文等宽 */}
              <span
                style={{
                  fontSize: 11,
                  color: 'var(--ocean-text-secondary)',
                  fontWeight: 500,
                  lineHeight: 1,
                  letterSpacing: 3,
                  width: 'fit-content',
                }}
              >
                工业研究智能平台
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

          <Layout style={{ background: 'transparent', marginLeft: 212 }}>
            <DynamicHeader user={user} onLogout={handleLogout} />

            <Content style={{ background: 'transparent', padding: '20px 0 0' }}>
              <ContentFrame>
                <Outlet />
              </ContentFrame>
            </Content>
          </Layout>
        </Layout>
      </PageHeaderProvider>
      <JobDrawer />
    </>
  );
}

/** 动态 Header：从 PageHeaderContext 读取当前页面注册的内容 */
function DynamicHeader({
  user,
  onLogout,
}: {
  user: { displayName: string };
  onLogout: () => void;
}): JSX.Element {
  const { header } = usePageHeader();
  const hasTabs = header.tabs && header.tabs.length > 0;

  return (
    <Header
      style={{
        display: 'flex',
        flexDirection: 'column',
        background: 'rgba(232, 246, 249, 0.5)',
        backdropFilter: 'blur(6px)',
        padding: '12px 24px',
        borderBottom: '1px solid var(--ocean-border-subtle)',
        position: 'sticky',
        top: 0,
        zIndex: 100,
        height: 'auto',
        lineHeight: 'normal',
        gap: 8,
      }}
    >
      {/* 第一行：英文索引(小字在上) + 中文标题(大字在下) + 右侧操作 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {header.index && (
            <Text
              style={{
                fontSize: 11,
                letterSpacing: 2,
                textTransform: 'uppercase',
                color: 'var(--ocean-text-muted)',
                fontFamily: 'var(--ocean-font-mono)',
                lineHeight: 1,
              }}
            >
              {header.index}
            </Text>
          )}
          {header.title && (
            <Title
              level={2}
              style={{
                margin: 0,
                fontSize: 32,
                fontWeight: 650,
                lineHeight: 1.15,
                color: 'var(--ocean-text-primary)',
              }}
            >
              {header.title}
            </Title>
          )}
        </div>
        <Space size="middle" align="center" style={{ paddingBottom: 4 }}>
          {header.actions}
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
          <Button type="link" onClick={onLogout}>
            登出
          </Button>
        </Space>
      </div>

      {/* 渐变分隔线：左深右浅 */}
      <div
        style={{
          height: 1,
          marginTop: 12,
          background: 'linear-gradient(to right, var(--ocean-border-subtle) 0%, transparent 100%)',
        }}
      />

      {/* 第二行：tabs（如果有）—— 胶囊式导航按钮，左边距与内容区对齐 */}
      <div style={{ display: 'flex', gap: 10, marginTop: 10, minHeight: hasTabs ? undefined : 28, paddingLeft: 'clamp(20px, 1.4vw, 32px)' }}>
        {hasTabs &&
          header.tabs!.map((tab) => {
            const isActive = header.activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => header.onTabChange?.(tab.key)}
                style={{
                  padding: '8px 20px',
                  fontSize: 14,
                  fontWeight: isActive ? 600 : 400,
                  color: isActive
                    ? 'var(--ocean-action-primary)'
                    : 'var(--ocean-text-secondary)',
                  background: isActive
                    ? 'rgba(20, 118, 214, 0.08)'
                    : 'transparent',
                  border: isActive
                    ? '1px solid rgba(20, 118, 214, 0.25)'
                    : '1px solid transparent',
                  borderRadius: 20,
                  cursor: 'pointer',
                  transition: 'all 200ms var(--ocean-motion-easing)',
                  lineHeight: 1.4,
                  whiteSpace: 'nowrap',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'rgba(20, 118, 214, 0.04)';
                    e.currentTarget.style.color = 'var(--ocean-text-primary)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'transparent';
                    e.currentTarget.style.color = 'var(--ocean-text-secondary)';
                  }
                }}
              >
                {tab.label}
              </button>
            );
          })}
      </div>
    </Header>
  );
}
