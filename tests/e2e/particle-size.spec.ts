/**
 * IRIP 端到端测试：黄金路径 UI 导航冒烟测试。
 *
 * 原始测试验证粒度实验数据上传→推导→审批完整流程，
 * 但 /ingestions 路由已删除（数据现在通过流程执行入库），
 * /provenance 和 /parameters 已重定向到 /lab-ops。
 *
 * 重建方案：
 *   简化为验证当前黄金路径的 UI 导航，不做完整的数据上传→推导→审批流程
 *   （完整流程需要 seed 数据和完整后端服务）。
 *
 * 覆盖场景：
 *   1. 管理员登录 → 实验室运营 → 参数管理 → 候选审批 → 查看完整来源
 *   2. 管理员登录 → 实验室运营 → 实验项目（流程执行入口）
 *   3. 管理员可以在实验室运营各 Tab 之间切换
 *
 * 认证策略：
 *   后端使用 rotating refresh tokens，storageState 无法跨 context 复用。
 *   本文件使用 serial 模式 + 共享 BrowserContext：beforeAll 登录一次，
 *   所有测试复用同一 context（cookie 在 context 内自动更新）。
 */

import { test, expect, type Page, type BrowserContext } from '@playwright/test';

/** 管理员凭据（bootstrap 创建，拥有全部权限） */
const ADMIN_EMAIL = 'admin@irip.local';
const ADMIN_PASSWORD = process.env.IRIP_BOOTSTRAP_ADMIN_PASSWORD ?? 'agsdgfsdg21r34sf';

/** 登录成功后允许的受保护路由前缀 */
const PROTECTED_ROUTE_RE = /\/(workbench|standards|lab-ops|platform)/;

// Serial 模式：所有测试共享同一个 BrowserContext，避免重复登录触发限流
test.describe.configure({ mode: 'serial' });

let sharedContext: BrowserContext;
let sharedPage: Page;

test.beforeAll(async ({ browser }) => {
  sharedContext = await browser.newContext({
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    locale: 'zh-CN',
  });
  sharedPage = await sharedContext.newPage();

  // 登录
  await sharedPage.goto('/login');
  await sharedPage.getByLabel('邮箱').fill(ADMIN_EMAIL);
  await sharedPage.getByLabel('密码').fill(ADMIN_PASSWORD);
  await sharedPage.getByRole('button', { name: /登\s*录/ }).click();
  await expect(sharedPage).toHaveURL(PROTECTED_ROUTE_RE, { timeout: 15000 });
});

test.afterAll(async () => {
  await sharedContext?.close();
});

test('管理员可以导航到参数管理并查看溯源链路', async () => {
  const page = sharedPage;

  // 导航到实验室运营 → 参数管理（衍生数据 Tab）
  await page.goto('/lab-ops?tab=parameters');
  await expect(page.getByRole('tab', { name: '参数列表' })).toBeVisible({ timeout: 10000 });

  // 如果有参数，点击"候选"查看审批面板
  const candidateButton = page.getByRole('button', { name: '候选' }).first();
  if (await candidateButton.isVisible({ timeout: 5000 })) {
    await candidateButton.click();

    // 验证审批面板出现
    await expect(page.getByText('候选版本审批')).toBeVisible({ timeout: 5000 });

    // 验证"查看完整来源"按钮存在（今天改为 button 不是 link）
    const viewSourceButton = page.getByRole('button', { name: '查看完整来源' }).first();
    await expect(viewSourceButton).toBeVisible({ timeout: 5000 });

    // 确保不是 link 角色
    await expect(page.getByRole('link', { name: '查看完整来源' })).toHaveCount(0);

    // 点击查看完整来源 → 应跳转到溯源链路
    await viewSourceButton.click();
    await page.waitForURL(/\/lab-ops\?.*tab=parameters/, { timeout: 10000 });

    // 验证溯源链路 Tab 可见
    await expect(page.getByRole('tab', { name: '溯源链路' })).toBeVisible({ timeout: 5000 });

    // 验证溯源图谱 Tab 可见（ProvenancePage 自动切换到 graph tab）
    await expect(page.getByRole('tab', { name: '溯源图谱' })).toBeVisible({ timeout: 5000 });
  }
});

test('管理员可以导航到实验项目并查看项目列表', async () => {
  const page = sharedPage;

  // 导航到实验室运营 → 实验项目（flows Tab）
  await page.goto('/lab-ops?tab=flows');

  // 验证"新建项目"按钮可见（ProjectList 组件独有，避免 "实验室运营" 文本歧义）
  await expect(page.getByRole('button', { name: '新建项目' })).toBeVisible({ timeout: 10000 });

  // 验证活跃/归档切换可见（Ant Design Radio.Button 的 input 是 hidden，用文本定位可见的 wrapper）
  await expect(page.getByText('活跃', { exact: true })).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('归档', { exact: true })).toBeVisible({ timeout: 5000 });
});

test('管理员可以在实验室运营各 Tab 之间切换', async () => {
  const page = sharedPage;

  // 从实验项目 Tab 开始
  await page.goto('/lab-ops?tab=flows');
  await expect(page.getByRole('button', { name: '新建项目' })).toBeVisible({ timeout: 10000 });

  // 切换到衍生数据 Tab（参数管理）
  await page.goto('/lab-ops?tab=parameters');
  await expect(page.getByRole('tab', { name: '参数列表' })).toBeVisible({ timeout: 10000 });

  // 验证溯源链路 Tab 存在
  await expect(page.getByRole('tab', { name: '溯源链路' })).toBeVisible({ timeout: 5000 });

  // 切换到模型发布 Tab
  await page.goto('/lab-ops?tab=models');
  // 模型发布 Tab 的 FeedbackState 渲染标题 "模型发布"，用 .first() 避免与导航菜单歧义
  await expect(page.getByText('模型发布').first()).toBeVisible({ timeout: 10000 });
});
