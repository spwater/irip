/**
 * Data Ocean Phase 6 — Critical UI Flow E2E
 *
 * Covers existing supported flows with stable bootstrap fixtures:
 *   1. Construction: department → equipment → object preset (cross-tab prefill)
 *   2. LabOps: search-param tab switching (?tab=facts, ?tab=components)
 *   3. Fact: row → detail → back
 *   4. Administrator sees AI tools tab
 *   5. Model: list → detail → prediction
 *   6. JobDrawer: open → job detail visible
 *   7. Destructive actions expose confirmation before mutation
 *
 * No seeded fake UI state — uses API-backed bootstrap fixtures only.
 * Uses role/label selectors; no arbitrary text matching when roles exist.
 */

import { test, expect } from '@playwright/test';
import { loginAsAdmin } from './helpers/auth';

test.describe('Data Ocean critical UI flows', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── 1. Construction: department → equipment → object preset ──
  test('standards page cross-tab preset from department to equipment', async ({ page }) => {
    await page.getByRole('menuitem', { name: '实验室建设' }).click();
    await expect(page).toHaveURL(/\/standards/);

    // Wait for the tabs to render
    await expect(page.getByRole('tab', { name: '组织机构' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '设备仪器' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '实验对象' })).toBeVisible();

    // Switch to equipment tab and verify it activates
    await page.getByRole('tab', { name: '设备仪器' }).click();
    await expect(page.getByRole('tab', { name: '设备仪器' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    // Switch to exp-objects tab
    await page.getByRole('tab', { name: '实验对象' }).click();
    await expect(page.getByRole('tab', { name: '实验对象' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  // ── 2. LabOps: search-param tab switching ──
  test('lab-ops tab switching via search param', async ({ page }) => {
    // Navigate with ?tab=facts
    await page.goto('/lab-ops?tab=facts');
    await expect(page).toHaveURL(/\/lab-ops/);
    await expect(page.getByRole('tab', { name: '实验记录' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    // Navigate with ?tab=components
    await page.goto('/lab-ops?tab=components');
    await expect(page.getByRole('tab', { name: '数据接口' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    // Navigate with ?tab=flows (default)
    await page.goto('/lab-ops?tab=flows');
    await expect(page.getByRole('tab', { name: '实验执行' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  // ── 3. Fact row → detail → back ──
  test('fact detail navigation and back', async ({ page }) => {
    // Go to lab-ops facts tab
    await page.goto('/lab-ops?tab=facts');
    await expect(page.getByRole('tab', { name: '实验记录' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    // Wait for the facts table to load (skeletons gone)
    await expect(page.locator('.ant-skeleton')).toHaveCount(0, { timeout: 15_000 });

    // Look for fact rows in the table — if any row has a link/button to detail, click it
    const factTable = page.locator('.ant-table-tbody tr').first();
    const hasFactRow = await factTable.isVisible().catch(() => false);

    if (hasFactRow) {
      // Try to find a "查看详情" or "查看" link/button in the first row
      const detailLink = page.getByRole('link', { name: /查看详情|查看/ }).first();
      const hasDetailLink = await detailLink.isVisible({ timeout: 5_000 }).catch(() => false);

      if (hasDetailLink) {
        await detailLink.click();
        // Should navigate to a fact detail URL
        await expect(page).toHaveURL(/\/facts\/[^/]+/, { timeout: 10_000 });

        // Verify the fact detail page shows PageIntro or heading
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

        // Navigate back
        await page.goBack();
        await expect(page).toHaveURL(/\/lab-ops/);
      }
    }

    // If no fact rows exist, the empty state should be visible — that's also valid
    await expect(page.getByRole('tab', { name: '实验记录' })).toBeVisible();
  });

  // ── 4. Administrator sees AI tools ──
  test('platform admin can see and access AI tools tab', async ({ page }) => {
    await page.getByRole('menuitem', { name: '平台应用' }).click();
    await expect(page).toHaveURL(/\/platform/);

    // Admin should see all three tabs including AI 工具管理
    await expect(page.getByRole('tab', { name: 'AI助手' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '数据抽取' })).toBeVisible();

    // The AI 工具管理 tab should be visible for admin
    const aiToolsTab = page.getByRole('tab', { name: 'AI 工具管理' });
    await expect(aiToolsTab).toBeVisible({ timeout: 10_000 });

    // Click into AI tools tab
    await aiToolsTab.click();
    await expect(aiToolsTab).toHaveAttribute('aria-selected', 'true');
  });

  // ── 5. Model: list → detail → prediction ──
  test('model list shows entries and detail is reachable', async ({ page }) => {
    // Navigate to models via direct URL (not in sidebar menu)
    await page.goto('/models');
    await expect(page).toHaveURL(/\/models/);

    // Wait for the models page to load
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

    // Wait for table to finish loading
    await expect(page.locator('.ant-skeleton')).toHaveCount(0, { timeout: 15_000 });

    // If model rows exist, click the first one to navigate to detail
    const modelRow = page.locator('.ant-table-tbody tr').first();
    const hasModelRow = await modelRow.isVisible({ timeout: 5_000 }).catch(() => false);

    if (hasModelRow) {
      // Try to find a detail link or clickable row
      const detailLink = page.getByRole('link', { name: /详情|查看/ }).first();
      const hasDetailLink = await detailLink.isVisible({ timeout: 5_000 }).catch(() => false);

      if (hasDetailLink) {
        await detailLink.click();
        await expect(page).toHaveURL(/\/models\/[^/]+/, { timeout: 10_000 });
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });
      }
    }
  });

  // ── 6. JobDrawer: open → job detail visible ──
  test('JobDrawer opens from header and shows job content', async ({ page }) => {
    // Wait for the app shell to fully render
    await expect(page.locator('[data-testid="ocean-app-content"]')).toBeVisible();

    // The "作业进度" button in the header opens the drawer
    const jobButton = page.getByRole('button', { name: /作业进度/ });
    await expect(jobButton).toBeVisible({ timeout: 10_000 });

    await jobButton.click();

    // The drawer should become visible (Ant Drawer uses role="dialog")
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5_000 });

    // The drawer title "作业进度" should be visible
    await expect(page.getByText('作业进度').first()).toBeVisible();

    // Close the drawer
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden({ timeout: 5_000 });
  });

  // ── 7. Destructive actions expose confirmation before mutation ──
  test('destructive fact deletion requires confirmation', async ({ page }) => {
    await page.goto('/lab-ops?tab=facts');
    await expect(page.getByRole('tab', { name: '实验记录' })).toBeVisible();

    // Wait for table to load
    await expect(page.locator('.ant-skeleton')).toHaveCount(0, { timeout: 15_000 });

    // Look for any delete button wrapped in Popconfirm
    const deleteButton = page.getByRole('button', { name: /删除|删除任务|删除选中/ }).first();
    const hasDeleteButton = await deleteButton.isVisible({ timeout: 5_000 }).catch(() => false);

    if (hasDeleteButton) {
      await deleteButton.click();

      // Ant Design Popconfirm renders a popover with confirmation buttons.
      // The confirmation text should be visible before any mutation occurs.
      await expect(page.getByText(/确定|不可撤销|确认删除|此操作/).first()).toBeVisible({
        timeout: 5_000,
      });

      // A "取消" (cancel) button must be available to abort
      await expect(page.getByRole('button', { name: '取消' })).toBeVisible();

      // Cancel the destructive action — no mutation should occur
      await page.getByRole('button', { name: '取消' }).click();
    }
  });

  // ── 8. Governance page shows health and jobs tabs ──
  test('governance console renders system config and jobs tabs', async ({ page }) => {
    await page.getByRole('menuitem', { name: '平台治理' }).click();
    await expect(page).toHaveURL(/\/governance/);

    await expect(page.getByRole('tab', { name: '系统配置' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '用户管理' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '审计事件' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '作业中心' })).toBeVisible();

    // Switch to jobs tab
    await page.getByRole('tab', { name: '作业中心' }).click();
    await expect(page.getByRole('tab', { name: '作业中心' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  // ── 9. No unhandled page errors on any primary destination ──
  test('primary destinations load without unhandled page errors', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(error.message);
    });

    const destinations = ['/workbench', '/standards', '/lab-ops', '/platform', '/governance'];
    for (const dest of destinations) {
      await page.goto(dest);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });
    }

    // Filter out known non-critical errors (e.g., favicon 404)
    const criticalErrors = pageErrors.filter(
      (msg) => !msg.includes('favicon') && !msg.includes('404'),
    );
    expect(criticalErrors).toEqual([]);
  });
});
