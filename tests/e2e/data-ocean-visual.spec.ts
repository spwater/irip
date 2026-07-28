/**
 * Data Ocean Phase 6 — Three-Viewport Visual Regression
 *
 * Captures stable full-page screenshots at 1280×800, 1440×900, and 1920×1080.
 * The playwright.config.ts defines three visual projects that exclusively run
 * this spec; the functional Chromium project ignores it.
 *
 * Stabilization before each screenshot:
 *   - emulateMedia({ reducedMotion: 'reduce' }) — disables CSS animations
 *   - page.clock.setFixedTime — eliminates timestamp drift
 *   - Wait for h1 to be visible and all Skeletons to disappear
 *
 * Baseline files are generated with --update-snapshots and require human
 * review before being committed.  Do NOT update baselines until a human has
 * inspected the intentional diff (Phase 6 Global Constraint).
 */

import { test, expect } from '@playwright/test';
import { loginAsAdmin } from './helpers/auth';

/**
 * Stabilize the page before capturing a screenshot.
 * Disables animations, fixes the clock, waits for content + skeleton-free state.
 */
async function stabilize(page: import('@playwright/test').Page): Promise<void> {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.setFixedTime(new Date('2026-07-28T06:00:00Z'));
  // Wait for the primary heading to appear (PageIntro h1)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 });
  // Wait for all Skeletons to disappear
  await expect(page.locator('.ant-skeleton')).toHaveCount(0, { timeout: 15_000 });
}

test.describe('Data Ocean visual regression', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── Login page ──
  test('login page', async ({ page }) => {
    // Log out to see the login page
    await page.goto('/login');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.clock.setFixedTime(new Date('2026-07-28T06:00:00Z'));
    await expect(page.getByText('IRIP 控制台')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('.ant-skeleton')).toHaveCount(0, { timeout: 5_000 });
    await expect(page).toHaveScreenshot('login.png', { fullPage: true, animations: 'disabled' });
  });

  // ── Workbench (研发看板) ──
  test('workbench page', async ({ page }) => {
    await page.goto('/workbench');
    await stabilize(page);
    await expect(page).toHaveScreenshot('workbench.png', { fullPage: true, animations: 'disabled' });
  });

  // ── Standards (实验室建设) ──
  test('standards page', async ({ page }) => {
    await page.goto('/standards');
    await stabilize(page);
    await expect(page).toHaveScreenshot('standards.png', { fullPage: true, animations: 'disabled' });
  });

  // ── LabOps — flows tab (实验执行) ──
  test('lab-ops flows tab', async ({ page }) => {
    await page.goto('/lab-ops?tab=flows');
    await stabilize(page);
    await expect(page).toHaveScreenshot('lab-ops-flows.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  // ── LabOps — facts tab (实验记录) ──
  test('lab-ops facts tab', async ({ page }) => {
    await page.goto('/lab-ops?tab=facts');
    await stabilize(page);
    await expect(page).toHaveScreenshot('lab-ops-facts.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  // ── LabOps — components tab (数据接口) ──
  test('lab-ops components tab', async ({ page }) => {
    await page.goto('/lab-ops?tab=components');
    await stabilize(page);
    await expect(page).toHaveScreenshot('lab-ops-components.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  // ── Platform — assistant tab (AI助手) ──
  test('platform assistant tab', async ({ page }) => {
    await page.goto('/platform?tab=assistant');
    await stabilize(page);
    await expect(page).toHaveScreenshot('platform-assistant.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  // ── Platform — parameters tab (数据抽取) ──
  test('platform parameters tab', async ({ page }) => {
    await page.goto('/platform?tab=parameters');
    await stabilize(page);
    await expect(page).toHaveScreenshot('platform-parameters.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  // ── Governance (平台治理) — system config tab ──
  test('governance system config tab', async ({ page }) => {
    await page.goto('/governance');
    await stabilize(page);
    await expect(page).toHaveScreenshot('governance-system-config.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  // ── Models list ──
  test('models list page', async ({ page }) => {
    await page.goto('/models');
    await stabilize(page);
    await expect(page).toHaveScreenshot('models-list.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  // ── Prediction workbench ──
  test('prediction workbench page', async ({ page }) => {
    await page.goto('/models/predict');
    await stabilize(page);
    await expect(page).toHaveScreenshot('prediction-workbench.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  // ── Jobs page ──
  test('jobs page', async ({ page }) => {
    await page.goto('/jobs');
    await stabilize(page);
    await expect(page).toHaveScreenshot('jobs-page.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });

  // ── JobDrawer open ──
  test('JobDrawer open from header', async ({ page }) => {
    await page.goto('/workbench');
    await stabilize(page);

    // Open the job drawer via the header button
    const jobButton = page.getByRole('button', { name: /作业进度/ });
    await expect(jobButton).toBeVisible({ timeout: 10_000 });
    await jobButton.click();

    // Wait for the drawer dialog to appear
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('.ant-skeleton')).toHaveCount(0, { timeout: 10_000 });

    // Mask the dynamic job progress bars and active count badges
    const progressBars = page.locator('.ant-progress');
    const count = await progressBars.count();

    await expect(page).toHaveScreenshot('job-drawer-open.png', {
      fullPage: true,
      animations: 'disabled',
      mask: count > 0 ? [progressBars] : undefined,
    });
  });
});
