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
import { FactsPage } from '@/pages/FactsPage';
import { StandardsPage } from '@/pages/StandardsPage';
import { ParametersPage } from '@/pages/ParametersPage';
import { ModelsPage } from '@/pages/ModelsPage';
import { GovernancePage } from '@/pages/GovernancePage';
import { JobsPage } from '@/pages/JobsPage';

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

  const factsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/facts',
    component: FactsPage,
  });

  const standardsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/standards',
    component: StandardsPage,
  });

  const parametersRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/parameters',
    component: ParametersPage,
  });

  const modelsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/models',
    component: ModelsPage,
  });

  const governanceRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/governance',
    component: GovernancePage,
  });

  const jobsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/jobs',
    component: JobsPage,
  });

  const routeTree = rootRoute.addChildren([
    loginRoute,
    indexRoute,
    protectedLayoutRoute.addChildren([
      workbenchRoute,
      factsRoute,
      standardsRoute,
      parametersRoute,
      modelsRoute,
      governanceRoute,
      jobsRoute,
    ]),
  ]);

  return createRouter({ routeTree });
}

/** 生产环境单例路由器 */
export const router = createAppRouter();
