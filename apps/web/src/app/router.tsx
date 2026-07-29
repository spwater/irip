import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from '@tanstack/react-router';
import { AuthProvider } from '@/auth/AuthProvider';
import { LoginPage } from '@/auth/LoginPage';
import { AppShell } from '@/app/AppShell';
import { WorkbenchPage } from '@/pages/WorkbenchPage';
import { StandardsPage as StandardsPageV1 } from '@/standards/StandardsPage';
import { LabOpsPage } from '@/pages/LabOpsPage';
import { PlatformPage } from '@/pages/PlatformPage';
import { FactDetail } from '@/facts/FactDetail';
import { ComponentsPage } from '@/components/ComponentsPage';
import { GovernanceConsole } from '@/governance/GovernanceConsole';
import { JobsPage } from '@/jobs/JobsPage';
import { JobDetail } from '@/jobs/JobDetail';

/**
 * 根路由布局 — 包裹 AuthProvider
 */
function RootLayout(): JSX.Element {
  return (
    <AuthProvider>
      <Outlet />
    </AuthProvider>
  );
}

/**
 * 创建应用路由器（每次调用生成新实例，测试用）
 */
export function createAppRouter() {
  const rootRoute = createRootRoute({
    component: RootLayout,
  });

  // /login — 登录页（无需认证）
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/login',
    component: LoginPage,
    validateSearch: (search: Record<string, unknown>): { redirect?: string } => ({
      redirect: typeof search.redirect === 'string' ? search.redirect : undefined,
    }),
  });

  // / — 重定向到 /workbench
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    beforeLoad: () => {
      throw redirect({ to: '/workbench' });
    },
  });

  // 受保护布局路由
  const protectedLayoutRoute = createRoute({
    getParentRoute: () => rootRoute,
    id: '_protected',
    component: AppShell,
  });

  const workbenchRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/workbench',
    component: WorkbenchPage,
  });

  // V1 routes — using new components
  const standardsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/standards',
    component: StandardsPageV1,
    validateSearch: (search: Record<string, unknown>): { tab?: string } => ({
      tab: typeof search.tab === 'string' ? search.tab : undefined,
    }),
  });

  const objectsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/objects',
    beforeLoad: () => {
      throw redirect({ to: '/standards' });
    },
  });

  const ingestionsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/ingestions',
    beforeLoad: () => {
      throw redirect({ to: '/facts' });
    },
  });

  const factsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/facts',
    beforeLoad: () => {
      throw redirect({ to: '/lab-ops' });
    },
  });

  const factDetailRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/facts/$factId',
    component: FactDetail,
  });

  const provenanceRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/provenance',
    beforeLoad: () => {
      throw redirect({ to: '/lab-ops' });
    },
  });

  const parametersRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/parameters',
    beforeLoad: () => {
      throw redirect({ to: '/lab-ops', search: { tab: 'parameters' } });
    },
  });

  // V2 组件管理路由
  const componentsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/components',
    component: ComponentsPage,
    validateSearch: (search: Record<string, unknown>): { prefill_object?: string; edit_id?: string } => ({
      prefill_object: typeof search.prefill_object === 'string' ? search.prefill_object : undefined,
      edit_id: typeof search.edit_id === 'string' ? search.edit_id : undefined,
    }),
  });

  // V2 流程编排路由
  const flowsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/flows',
    beforeLoad: () => {
      throw redirect({ to: '/lab-ops' });
    },
  });

  // 实验室运营页面（Tab：实验任务 / 原始数据 / 衍生数据）
  // 支持 ?tab= 搜索参数，便于从详情页（如事实详情）深链回指定 Tab
  const labOpsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/lab-ops',
    component: LabOpsPage,
    validateSearch: (search: Record<string, unknown>): { tab?: string } => ({
      tab: typeof search.tab === 'string' ? search.tab : undefined,
    }),
  });

  // 平台应用页面（Tab：AI 助手 / AI 工具管理 / 数据接口）
  const platformRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/platform',
    component: PlatformPage,
    validateSearch: (search: Record<string, unknown>): { tab?: string; prefill_object?: string; edit_id?: string } => ({
      tab: typeof search.tab === 'string' ? search.tab : undefined,
      prefill_object: typeof search.prefill_object === 'string' ? search.prefill_object : undefined,
      edit_id: typeof search.edit_id === 'string' ? search.edit_id : undefined,
    }),
  });

  // V3 平台治理路由（Tabs 内部切换子页面）
  const governanceRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/governance',
    component: GovernanceConsole,
  });

  const jobsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/jobs',
    component: JobsPage,
  });

  const jobDetailRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/jobs/$jobId',
    component: JobDetail,
  });

  const routeTree = rootRoute.addChildren([
    loginRoute,
    indexRoute,
    protectedLayoutRoute.addChildren([
      workbenchRoute,
      standardsRoute,
      objectsRoute,
      ingestionsRoute,
      factsRoute,
      factDetailRoute,
      provenanceRoute,
      parametersRoute,
      componentsRoute,
      flowsRoute,
      labOpsRoute,
      platformRoute,
      governanceRoute,
      jobsRoute,
      jobDetailRoute,
    ]),
  ]);

  return createRouter({ routeTree });
}

/** 生产环境单例路由器 */
export const router = createAppRouter();
