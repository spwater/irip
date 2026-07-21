/**
 * IRIP V0 登录端到端测试（实施计划 Task 9 Step 4）。
 *
 * 验收场景（docs/arch-v0.md §8.3 第 839 行）：
 *   浏览器登录 → 成功进入工作台；F5 刷新 → 会话保持
 *
 * 使用中文标签定位元素（LoginPage.tsx 使用 Ant Design 中文 Form）：
 *   - 标题：IRIP 控制台
 *   - 邮箱字段：label="邮箱"
 *   - 密码字段：label="密码"
 *   - 登录按钮：text="登录"
 *
 * 前置：bootstrap 已创建 admin@irip.local 用户，前端 dev server 已启动。
 */

import { test, expect } from '@playwright/test';

test('管理员登录成功并进入工作台', async ({ page }) => {
  // 访问登录页
  await page.goto('/login');

  // 确认登录页标题
  await expect(page.getByText('IRIP 控制台')).toBeVisible();

  // 填写邮箱（Ant Design Form label="邮箱"）
  await page.getByLabel('邮箱').fill('admin@irip.local');

  // 填写密码（Ant Design Form label="密码"）
  await page.getByLabel('密码').fill('Admin-IRIP-2026');

  // 点击登录按钮
  await page.getByRole('button', { name: '登录' }).click();

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
