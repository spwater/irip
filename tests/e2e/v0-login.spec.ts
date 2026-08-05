/**
 * IRIP V0 登录端到端测试。
 *
 * 验收场景：
 *   浏览器登录 → 成功进入工作台；登录失败 → 显示错误提示
 *
 * 使用中文标签定位元素（LoginPage.tsx 使用 Ant Design 中文 Form）：
 *   - 标题：IRIP 控制台
 *   - 邮箱字段：label="邮箱"
 *   - 密码字段：label="密码"
 *   - 登录按钮：text="登录"（letterSpacing 不影响 textContent）
 *
 * 路由说明（2025 重建后）：
 *   登录成功后默认重定向到 /workbench（研发看板），该路由仍然有效。
 *   其余受保护路由：/standards（实验室建设）、/lab-ops（实验室运营）、/platform（平台应用）。
 *
 * 前置：bootstrap 已创建 admin@irip.local 用户，前端 dev server 已启动。
 */

import { test, expect } from '@playwright/test';

/** 管理员凭据（bootstrap 创建） */
const ADMIN_EMAIL = 'admin@irip.local';
const ADMIN_PASSWORD = 'agsdgfsdg21r34sf';

/** 登录成功后允许的受保护路由前缀 */
const PROTECTED_ROUTE_RE = /\/(workbench|standards|lab-ops|platform)/;

test('管理员登录成功并进入工作台', async ({ page }) => {
  // 访问登录页
  await page.goto('/login');

  // 确认登录页标题
  await expect(page.getByText('IRIP 控制台')).toBeVisible();

  // 填写邮箱（Ant Design Form label="邮箱"）
  await page.getByLabel('邮箱').fill(ADMIN_EMAIL);

  // 填写密码（Ant Design Form label="密码"）
  await page.getByLabel('密码').fill(ADMIN_PASSWORD);

  // 点击登录按钮（letterSpacing:4 不影响 textContent，但用正则兼容间距渲染）
  await page.getByRole('button', { name: /登\s*录/ }).click();

  // 验证已跳转到受保护路由（默认 /workbench），给后端登录 API 充分响应时间
  await expect(page).toHaveURL(PROTECTED_ROUTE_RE, { timeout: 15000 });
});

test('登录失败显示错误提示', async ({ page }) => {
  await page.goto('/login');

  await page.getByLabel('邮箱').fill(ADMIN_EMAIL);
  await page.getByLabel('密码').fill('Wrong-Password-2026');
  await page.getByRole('button', { name: /登\s*录/ }).click();

  // 验证错误提示（LoginPage 显示 message.error('登录失败，请检查邮箱和密码')）
  await expect(page.getByText('登录失败')).toBeVisible({ timeout: 10000 });
});
