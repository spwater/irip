/**
 * IRIP 端到端测试：参数溯源链完整性验证。
 *
 * 验收场景：
 *   从参数列表 → 点击"候选" → 审批面板 → 点击"查看完整来源"(button)
 *   → 跳转到 /lab-ops?tab=parameters&provenance_run_id=xxx → 溯源链路 Tab → 溯源图谱
 *
 * 路由说明（2025 重建后）：
 *   - /parameters 已重定向到 /lab-ops?tab=parameters
 *   - ApprovalPanel 的"查看完整来源"从 <a href> 改为 <Button onClick navigate>
 *   - 参数列表操作列按钮文本为"候选"（非"查看候选"）
 *   - 审批抽屉标题为"候选版本审批"
 *   - 溯源节点类型标签：fact_revision → "事实版本"（非"事实修订"）
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

test('parameter provenance: 查看完整来源按钮跳转到溯源链路', async () => {
  const page = sharedPage;

  // 导航到参数管理（/parameters 已重定向到 /lab-ops?tab=parameters）
  await page.goto('/lab-ops?tab=parameters');

  // 验证参数列表 Tab 可见（ParameterPage 的 Tab 标签）
  await expect(page.getByRole('tab', { name: '参数列表' })).toBeVisible({ timeout: 10000 });

  // 查找第一个"候选"按钮（操作列）
  const candidateButton = page.getByRole('button', { name: '候选' }).first();

  // 如果有参数数据，进行候选审批 + 溯源验证
  if (await candidateButton.isVisible({ timeout: 8000 })) {
    await candidateButton.click();

    // 验证审批抽屉出现
    await expect(page.getByText('候选版本审批')).toBeVisible({ timeout: 5000 });

    // 验证"查看完整来源"是 Button（今天从 link 改为 button + onClick navigate）
    const viewSourceButton = page.getByRole('button', { name: '查看完整来源' });
    await expect(viewSourceButton.first()).toBeVisible({ timeout: 5000 });

    // 确保不是 link 角色
    await expect(page.getByRole('link', { name: '查看完整来源' })).toHaveCount(0);

    // 点击查看完整来源
    await viewSourceButton.first().click();

    // 验证 URL 变化：应包含 tab=parameters 和 provenance_run_id（如果有 derivation_run_id）
    await page.waitForURL(/\/lab-ops\?.*tab=parameters/, { timeout: 10000 });

    // 验证溯源链路 Tab 被激活（ParameterPage 自动切换到 provenance tab）
    await expect(page.getByRole('tab', { name: '溯源链路' })).toBeVisible({ timeout: 5000 });

    // 验证溯源图谱 Tab 可见（ProvenancePage 自动切换到 graph tab）
    await expect(page.getByRole('tab', { name: '溯源图谱' })).toBeVisible({ timeout: 5000 });
  } else {
    // 无参数数据时，验证页面基本结构即可
    await expect(page.getByRole('tab', { name: '参数列表' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '溯源链路' })).toBeVisible();
  }
});

test('parameter approval: 管理员可在审批面板查看候选详情', async () => {
  const page = sharedPage;

  await page.goto('/lab-ops?tab=parameters');
  await expect(page.getByRole('tab', { name: '参数列表' })).toBeVisible({ timeout: 10000 });

  const candidateButton = page.getByRole('button', { name: '候选' }).first();

  if (await candidateButton.isVisible({ timeout: 8000 })) {
    await candidateButton.click();
    await expect(page.getByText('候选版本审批')).toBeVisible({ timeout: 5000 });

    // 管理员拥有 parameter:approve 权限，且不是提交者：
    // 如果候选处于 in_review 状态，应能看到"批准发布"按钮
    // 如果候选不在 in_review 状态，应看到提示文本
    const approveButton = page.getByRole('button', { name: '批准发布' });
    const canApprove = await approveButton.isVisible({ timeout: 3000 }).catch(() => false);

    if (canApprove) {
      // 管理员可以审批：验证批准和驳回按钮都在
      await expect(approveButton.first()).toBeVisible();
      await expect(page.getByRole('button', { name: '驳回' }).first()).toBeVisible();
    } else {
      // 候选不在待审批状态，应显示提示文本
      const hintTexts = [
        '提交者不可审批自己提交的候选参数',
        '当前账号无参数审批权限',
        '该候选不在待审批状态',
        '暂不可审批',
      ];
      const hasHint = await Promise.all(
        hintTexts.map((t) => page.getByText(t).isVisible().catch(() => false)),
      );
      expect(hasHint.some(Boolean)).toBeTruthy();
    }

    // "查看完整来源"按钮始终可见（无论审批状态）
    await expect(page.getByRole('button', { name: '查看完整来源' }).first()).toBeVisible({ timeout: 5000 });
  } else {
    // 无参数数据，跳过此测试场景
    test.skip();
  }
});
