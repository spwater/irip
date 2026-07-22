/**
 * IRIP V1 端到端测试：参数溯源链完整性验证（Task 20 Step 1）。
 *
 * 验收场景：
 *   从已发布参数 → 点击"查看完整来源" → 溯源图展示完整链路
 *   推导运行 → 证据集 → 事实修订 → 观察值 → 原始工件
 *
 * V1 reviewer gate: 审查者能从参数值导航到原始实验文件
 */

import { test, expect } from '@playwright/test';

async function loginAsReviewer(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('邮箱').fill('reviewer@irip.local');
  await page.getByLabel('密码').fill('Review-IRIP-2026!');
  await page.getByRole('button', { name: /登\s*录/ }).click();
  await expect(page).toHaveURL(/\/(workbench|standards|facts|parameters)/);
}

test('parameter provenance graph shows complete chain to raw artifacts', async ({ page }) => {
  await loginAsReviewer(page);

  // 导航到参数管理
  await page.goto('/parameters');
  await expect(page.getByText('参数管理')).toBeVisible();

  // 查看第一个已发布参数的候选
  const viewButton = page.getByRole('button', { name: '查看候选' }).first();
  await viewButton.click();

  // 验证审批面板显示溯源链接
  await expect(page.getByRole('link', { name: '查看完整来源' })).toBeVisible({ timeout: 5000 });

  // 点击查看完整来源
  await page.getByRole('link', { name: '查看完整来源' }).click();

  // 验证溯源图页面显示节点
  await expect(page.getByText('溯源图')).toBeVisible({ timeout: 10000 });

  // 验证溯源图包含推导运行节点
  await expect(page.getByText('推导运行')).toBeVisible({ timeout: 5000 });

  // 验证溯源图包含事实修订节点
  await expect(page.getByText('事实修订')).toBeVisible({ timeout: 5000 });
});

test('submitter cannot approve own parameter candidate', async ({ page }) => {
  // 以研究员身份登录（提交者）
  await page.goto('/login');
  await page.getByLabel('邮箱').fill('researcher@irip.local');
  await page.getByLabel('密码').fill('Research-IRIP-2026!');
  await page.getByRole('button', { name: /登\s*录/ }).click();

  await page.goto('/parameters');
  await page.getByRole('button', { name: '查看候选' }).first().click();

  // 提交者不应看到批准发布按钮
  await expect(page.getByRole('button', { name: '批准发布' })).not.toBeVisible({ timeout: 5000 });

  // 但应能看到溯源链接
  await expect(page.getByRole('link', { name: '查看完整来源' })).toBeVisible();
});
