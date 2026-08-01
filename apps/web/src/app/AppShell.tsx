import { useEffect, useState } from 'react';
import { Avatar, Button, Grid, Layout, Menu, Space, Typography } from 'antd';
import { Outlet, useLocation, useNavigate } from '@tanstack/react-router';
import type { MenuProps } from 'antd';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { JobDrawer, JobDrawerButton } from '@/features/jobs/JobDrawer';
import { OceanBackdrop } from '@/shared/layout/OceanBackdrop';
import { ContentFrame } from '@/shared/layout/ContentFrame';
import { PageHeaderProvider, usePageHeader } from './PageHeaderContext';

const { Sider, Header, Content } = Layout;
const { Title, Text } = Typography;

/** 一级导航元数据：中文名 + 编号 + 英文小标（潮线签名结构） */
const NAV_META: ReadonlyArray<{ key: string; label: string; num: string; en: string }> = [
  { key: '/workbench', label: '研发看板', num: '01', en: 'Board' },
  { key: '/standards', label: '实验室建设', num: '02', en: 'Build' },
  { key: '/lab-ops', label: '实验室运营', num: '03', en: 'Ops' },
  { key: '/platform', label: '平台应用', num: '04', en: 'Apps' },
  { key: '/governance', label: '平台治理', num: '05', en: 'Gov' },
];

/** 导航菜单项：编号 + 中文 + 英文小标 */
const NAV_ITEMS: MenuProps['items'] = NAV_META.map((item) => ({
  key: item.key,
  label: (
    <span className="ocean-nav-item">
      <span className="ocean-nav-item__num">{item.num}</span>
      <span className="ocean-nav-item__label">{item.label}</span>
      <span className="ocean-nav-item__en">{item.en}</span>
    </span>
  ),
}));

/** 路由前缀 → 水印英文（页头背后的超大描边字） */
const HEADER_WATERMARK: Record<string, string> = {
  '/workbench': 'Workbench',
  '/standards': 'Laboratory',
  '/lab-ops': 'Operations',
  '/platform': 'Platform',
  '/governance': 'Governance',
};

/**
 * 主布局「潮线 Tideline」：
 * OceanBackdrop 包裹 → AppShell
 *   ├─ Sider（深潮品牌块 + 编号导航 + 斜切选中带）
 *   ├─ Layout
 *   │  ├─ Header（水印大字 + 深蓝标题 + 刻度线）
 *   │  └─ Content（ContentFrame → Outlet）
 *   └─ JobDrawer
 */
export function AppShell(): JSX.Element | null {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const location = useLocation();
  // L-02: 跟踪 Sider 折叠状态，动态调整内容区 marginLeft
  const screens = Grid.useBreakpoint();
  const [siderCollapsed, setSiderCollapsed] = useState<boolean>(false);
  // 当屏幕 < lg（992px）时 Sider 折叠，内容区 marginLeft 应为 0
  // screens.lg === false 表示明确低于 lg；undefined（首屏未测量）时默认不折叠
  const contentMarginLeft: number = siderCollapsed || screens.lg === false ? 0 : 216;

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

  const matchedMeta = NAV_META.find((item) => location.pathname.startsWith(item.key));
  const selectedKey: string = matchedMeta ? matchedMeta.key : location.pathname;
  const watermark: string | undefined = HEADER_WATERMARK[selectedKey];

  return (
    <>
      <OceanBackdrop />
      <PageHeaderProvider>
        <Layout style={{ minHeight: '100vh', background: 'transparent', position: 'relative', zIndex: 10 }}>
          {/* 导航：固定定位，不随滚动 */}
          <Sider
            width={216}
            breakpoint="lg"
            collapsedWidth={0}
            theme="light"
            onCollapse={setSiderCollapsed}
            style={{
              background: 'linear-gradient(to bottom, rgba(166, 211, 230, 0.55) 0px, rgba(234, 246, 249, 0.12) 180px, rgba(234, 246, 249, 0) 320px)',
              backgroundRepeat: 'no-repeat',
              backgroundSize: '100% 100%',
              backgroundPosition: '0 0',
              borderRight: '1px solid rgba(11, 74, 111, 0.10)',
              position: 'fixed',
              left: 0,
              top: 0,
              bottom: 0,
              overflow: 'auto',
              zIndex: 200,
            }}
          >
            {/* 品牌块：深潮渐变 + 斜切底边 + 亮青顶线 */}
            <div
              className="ocean-abyss-block ocean-tide-enter"
              style={{
                margin: '14px 12px 18px',
                padding: '18px 18px 26px',
                clipPath: 'polygon(0 0, 100% 0, 100% calc(100% - 18px), calc(100% - 18px) 100%, 0 100%)',
                boxShadow: '0 14px 32px rgba(7, 51, 78, 0.30)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span
                  style={{
                    fontSize: 30,
                    fontWeight: 800,
                    letterSpacing: 2,
                    color: '#EAF6F9',
                    lineHeight: 1,
                  }}
                >
                  IRIP
                </span>
                <span
                  style={{
                    fontSize: 9,
                    letterSpacing: 2,
                    textTransform: 'uppercase',
                    color: 'var(--ocean-current-on-deep)',
                    fontFamily: 'var(--ocean-font-mono)',
                    fontWeight: 600,
                    lineHeight: 1,
                  }}
                >
                  Data Ocean
                </span>
              </div>
              <span
                style={{
                  display: 'block',
                  marginTop: 8,
                  fontSize: 11,
                  color: 'rgba(234, 246, 249, 0.82)',
                  fontWeight: 500,
                  lineHeight: 1,
                  letterSpacing: 3,
                }}
              >
                工业研究智能平台
              </span>
              {/* 装饰坐标行 */}
              <span
                style={{
                  display: 'block',
                  marginTop: 12,
                  fontSize: 8,
                  letterSpacing: 1.6,
                  color: 'rgba(79, 224, 236, 0.55)',
                  fontFamily: 'var(--ocean-font-mono)',
                }}
              >
                31.23°N · TIDE-07 · ALPHA
              </span>
            </div>

            <Menu
              mode="inline"
              selectedKeys={[selectedKey]}
              items={NAV_ITEMS}
              onClick={handleMenuClick}
              className="ocean-sider-menu"
              style={{
                background: 'transparent',
                borderInlineEnd: 'none',
                padding: '0 12px',
              }}
            />

            {/* 底部状态行 */}
            <div
              style={{
                position: 'absolute',
                bottom: 14,
                left: 20,
                right: 20,
                display: 'flex',
                justifyContent: 'space-between',
                fontFamily: 'var(--ocean-font-mono)',
                fontSize: 9,
                letterSpacing: 1.5,
                color: 'var(--ocean-text-muted)',
                textTransform: 'uppercase',
              }}
            >
              <span>v0.2.0</span>
              <span>Sea of Data</span>
            </div>
          </Sider>

          <Layout style={{ marginLeft: contentMarginLeft, transition: 'margin-left 200ms var(--ocean-motion-easing)' }}>
            <DynamicHeader user={user} onLogout={handleLogout} watermark={watermark} />

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

/** 动态 Header：水印大字 + 深蓝标题 + 刻度线 + 右侧操作 */
function DynamicHeader({
  user,
  onLogout,
  watermark,
}: {
  user: { displayName: string };
  onLogout: () => void;
  watermark?: string;
}): JSX.Element {
  const { header } = usePageHeader();
  const hasTabs = header.tabs && header.tabs.length > 0;
  const isHero = header.heroTitle === true;

  return (
    <Header
      style={{
        display: 'flex',
        flexDirection: 'column',
        background: 'linear-gradient(to bottom, rgba(203, 228, 238, 0.72) 0px, rgba(234, 246, 249, 0.18) 120px, rgba(234, 246, 249, 0) 170px)',
        backdropFilter: 'blur(8px)',
        padding: isHero ? '16px 24px' : '16px 24px',
        borderBottom: 'none',
        position: 'sticky',
        top: 0,
        zIndex: 100,
        height: 'auto',
        lineHeight: 'normal',
        gap: 0,
        overflow: 'hidden',
      }}
    >
      {/* 水印大字：当前模块英文，超大描边空心 */}
      {watermark && (
        <span
          className="ocean-watermark"
          style={{
            right: -12,
            top: '50%',
            transform: 'translateY(-52%)',
            fontSize: 'clamp(88px, 9vw, 168px)',
          }}
          aria-hidden="true"
        >
          {watermark}
        </span>
      )}

      {/* 第一行：英文索引(小字在上) + 中文标题(大字在下) + 右侧操作 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flex: isHero ? 1 : undefined, flexWrap: 'wrap', position: 'relative', zIndex: 1 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, justifyContent: isHero ? 'center' : undefined, height: isHero ? '100%' : undefined }}>
          {header.index && (
            <Text
              style={{
                fontSize: 12,
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
            <div style={{ display: 'flex', alignItems: 'stretch', gap: 14 }}>
              {/* 斜切题注条 */}
              <span
                aria-hidden="true"
                style={{
                  width: 6,
                  background: 'var(--ocean-abyss-gradient)',
                  transform: 'skewX(-10deg)',
                  boxShadow: '2px 0 0 rgba(23, 184, 206, 0.55)',
                }}
              />
              <Title
                level={2}
                style={{
                  margin: 0,
                  fontSize: isHero ? 44 : 44,
                  fontWeight: 800,
                  lineHeight: 1.12,
                  letterSpacing: '0.06em',
                  color: 'var(--ocean-abyss-deep)',
                }}
              >
                {header.title}
              </Title>
            </div>
          )}
        </div>
        <Space size="middle" align="center" style={{ paddingBottom: 4 }}>
          {header.actions}
          <JobDrawerButton />
          <Space size="small" align="center">
            <Avatar
              size="small"
              style={{
                backgroundColor: 'var(--ocean-abyss-deep)',
                color: '#4FE0EC',
                fontWeight: 700,
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

      {/* 第二行：tabs（如果有）—— 斜切胶囊导航，左边距与内容区对齐（hero模式不显示） */}
      {!isHero && (
        <div style={{ display: 'flex', gap: 10, marginTop: 12, marginBottom: -24, minHeight: hasTabs ? undefined : 42, paddingLeft: 'clamp(20px, 1.4vw, 32px)', position: 'relative', zIndex: 1 }}>
        {hasTabs &&
          header.tabs!.map((tab) => {
            const isActive = header.activeTab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                aria-label={tab.label}
                aria-pressed={isActive}
                onClick={() => header.onTabChange?.(tab.key)}
                style={{
                  padding: '8px 22px',
                  fontSize: 14,
                  fontWeight: isActive ? 600 : 400,
                  color: isActive ? '#EAF6F9' : 'var(--ocean-text-secondary)',
                  background: isActive ? 'var(--ocean-abyss-gradient-x)' : 'transparent',
                  border: isActive ? 'none' : '1px solid rgba(11, 74, 111, 0.16)',
                  clipPath: 'polygon(8px 0, 100% 0, calc(100% - 8px) 100%, 0 100%)',
                  borderRadius: 0,
                  cursor: 'pointer',
                  transition: 'all 200ms var(--ocean-motion-easing)',
                  lineHeight: 1.4,
                  whiteSpace: 'nowrap',
                  boxShadow: isActive ? '0 6px 16px rgba(7, 51, 78, 0.24)' : 'none',
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'rgba(11, 74, 111, 0.06)';
                    e.currentTarget.style.color = 'var(--ocean-abyss-deep)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'transparent';
                    e.currentTarget.style.color = 'var(--ocean-text-secondary)';
                  }
                }}
                onFocus={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'rgba(11, 74, 111, 0.06)';
                    e.currentTarget.style.color = 'var(--ocean-abyss-deep)';
                  }
                }}
                onBlur={(e) => {
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
      )}
    </Header>
  );
}
