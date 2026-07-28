/**
 * IRIP V0 登录端到端测试（实施计划 Task 9 Step 4）。
 *
 * 验收场景（docs/arch-v0.md §8.3 第 839 行）：
 *   浏览器登录 → 成功进入工作台；F5 刷新 → 会话保持
 *
 * Phase 6: the success scenario now reuses the shared loginAsAdmin helper.
 * The failure scenario remains inline to verify the error path explicitly.
 *
 * 前置：bootstrap 已创建 admin@irip.local 用户，前端 dev server 已启动。
 */

import { test, expect } from '@playwright/test';
import { loginAsAdmin } from './helpers/auth';

test('管理员登录成功并进入工作台', async ({ page }) => {
  // Reuse the shared helper — if this succeeds the workbench is visible
  await loginAsAdmin(page);

  // 验证已跳转到工作台
  await expect(page).toHaveURL(/\/workbench/);
});

test('登录失败显示错误提示', async ({ page }) => {
  await page.goto('/login');

  await page.getByLabel('邮箱').fill('admin@irip.local');
  await page.getByLabel('密码').fill('Wrong-Password-2026');
  await page.getByRole('button', { name: '登录' }).click();

  // 验证错误提示
  await expect(page.getByText('登录失败')).toBeVisible();
});
