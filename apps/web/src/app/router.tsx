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
import { FactsPage as FactsPageV1 } from '@/facts/FactsPage';
import { FactDetail } from '@/facts/FactDetail';
import { ParameterPage as ParameterPageV1 } from '@/parameters/ParameterPage';
import { ComponentsPage } from '@/components/ComponentsPage';
import { FlowDetail } from '@/components/FlowDetail';
import { ModelsPage } from '@/models/ModelsPage';
import { ModelDetail } from '@/models/ModelDetail';
import { PredictionWorkbench } from '@/models/PredictionWorkbench';
import { AssistantPage } from '@/assistant/AssistantPage';
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
    component: FactsPageV1,
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
      throw redirect({ to: '/parameters' });
    },
  });

  const parametersRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/parameters',
    component: ParameterPageV1,
  });

  // V2 模型管理路由（替换 V0 占位页面）
  const modelsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/models',
    component: ModelsPage,
  });

  const modelDetailRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/models/$modelId',
    component: ModelDetail,
  });

  const predictionRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/models/predict',
    component: PredictionWorkbench,
    validateSearch: (search: Record<string, unknown>): { modelId?: string } => ({
      modelId: typeof search.modelId === 'string' ? search.modelId : undefined,
    }),
  });

  // V2 组件管理路由
  const componentsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/components',
    component: ComponentsPage,
  });

  // V2 流程编排路由
  const flowsRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/flows',
    component: FlowDetail,
  });

  // V3 AI 助手路由
  const assistantRoute = createRoute({
    getParentRoute: () => protectedLayoutRoute,
    path: '/assistant',
    component: AssistantPage,
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
      modelsRoute,
      modelDetailRoute,
      predictionRoute,
      assistantRoute,
      governanceRoute,
      jobsRoute,
      jobDetailRoute,
    ]),
  ]);

  return createRouter({ routeTree });
}

/** 生产环境单例路由器 */
export const router = createAppRouter();
