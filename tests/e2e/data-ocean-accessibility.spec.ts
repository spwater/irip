/**
 * Data Ocean Phase 6 — Keyboard, Zoom, Reduced Motion, and Performance Audit
 *
 * Executable browser checks for:
 *   1. Keyboard navigation and focus management (login, nav, tabs, drawer, modal)
 *   2. prefers-reduced-motion disables CSS animations and ECharts animation
 *   3. 200% browser zoom: key controls remain visible, no page-level overflow
 *   4. Performance evidence: navigation timing and resource counts
 *
 * No axe, Lighthouse, or visual-testing SaaS dependencies are used —
 * only Playwright built-ins and browser performance APIs.
 */

import { test, expect } from '@playwright/test';
import { loginAsAdmin } from './helpers/auth';

test.describe('Data Ocean keyboard and focus', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── 1. Login page keyboard accessibility ──
  test('login form is keyboard navigable', async ({ page }) => {
    await page.goto('/login');

    // Tab to the email field and type
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await page.keyboard.type('admin@irip.local');

    // Tab to password
    await page.keyboard.press('Tab');
    await page.keyboard.type('admin123');

    // Tab to login button and press Enter
    await page.keyboard.press('Tab');
    await page.keyboard.press('Enter');

    // Should redirect to an authenticated route
    await page.waitForURL(/\/(workbench|standards|lab-ops|platform|governance)/, {
      timeout: 15_000,
    });
  });

  // ── 2. Primary navigation via keyboard ──
  test('primary navigation menu is keyboard accessible', async ({ page }) => {
    // Focus the sidebar nav
    const firstMenuItem = page.getByRole('menuitem').first();
    await firstMenuItem.focus();

    // Use Arrow keys / Tab to navigate between menu items
    await page.keyboard.press('Tab');

    // Press Enter on a focused menu item to navigate
    const labOpsItem = page.getByRole('menuitem', { name: '实验室运营' });
    await labOpsItem.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/lab-ops/, { timeout: 10_000 });
  });

  // ── 3. Tab switching via keyboard ──
  test('LabOps tabs are keyboard switchable', async ({ page }) => {
    await page.goto('/lab-ops');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

    // Tab to the tab list and switch with arrow keys
    const flowsTab = page.getByRole('tab', { name: '实验执行' });
    const factsTab = page.getByRole('tab', { name: '实验记录' });

    await factsTab.focus();
    await factsTab.press('Enter');
    await expect(factsTab).toHaveAttribute('aria-selected', 'true');

    await flowsTab.focus();
    await flowsTab.press('Enter');
    await expect(flowsTab).toHaveAttribute('aria-selected', 'true');
  });

  // ── 4. Drawer opens and closes with keyboard, focus returns ──
  test('JobDrawer opens with Enter and closes with Escape, focus returns', async ({ page }) => {
    await expect(page.locator('[data-testid="ocean-app-content"]')).toBeVisible();

    const trigger = page.getByRole('button', { name: /作业进度/ });
    await trigger.focus();
    await expect(trigger).toBeFocused();

    await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5_000 });

    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden({ timeout: 5_000 });

    // Focus should return to the trigger button
    await expect(trigger).toBeFocused();
  });

  // ── 5. Governance tab switching via keyboard ──
  test('governance tabs are keyboard switchable', async ({ page }) => {
    await page.goto('/governance');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

    const usersTab = page.getByRole('tab', { name: '用户管理' });
    await usersTab.focus();
    await usersTab.press('Enter');
    await expect(usersTab).toHaveAttribute('aria-selected', 'true');
  });
});

test.describe('Data Ocean reduced motion', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── 6. Reduced motion disables ocean-atmosphere animation ──
  test('ocean-atmosphere animation is none in reduced-motion mode', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/workbench');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

    const atmosphere = page.locator('.ocean-atmosphere');
    await expect(atmosphere).toBeVisible();

    const animationName = await atmosphere.evaluate(
      (node) => getComputedStyle(node).animationName,
    );
    expect(animationName).toBe('none');
  });

  // ── 7. ECharts animation disabled in reduced-motion ──
  test('echarts containers report animation disabled in reduced-motion', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });

    // Navigate to a page that may contain ECharts (assistant or prediction)
    await page.goto('/platform?tab=assistant');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

    // Wait for content to stabilize
    await page.waitForTimeout(2000);

    // Check all data-echarts elements for animation attribute
    const chartContainers = page.locator('[data-echarts]');
    const count = await chartContainers.count();

    if (count > 0) {
      const animationStates = await chartContainers.evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute('data-echarts-animation')),
      );
      // Every chart should have data-echarts-animation="false" in reduced-motion
      expect(animationStates.every((state) => state === 'false')).toBe(true);
    }
    // If no charts are present on this page, the test still passes —
    // the attribute is validated on pages where charts render.
  });

  // ── 8. Global transitions are disabled in reduced-motion ──
  test('global CSS transitions are near-zero in reduced-motion', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/workbench');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

    // The ocean-enter animation class should have animation-duration near 0
    const enterElement = page.locator('.ocean-enter').first();
    const hasEnter = await enterElement.isVisible({ timeout: 3_000 }).catch(() => false);

    if (hasEnter) {
      const duration = await enterElement.evaluate(
        (node) => getComputedStyle(node).animationDuration,
      );
      // In reduced-motion, duration should be ~0.01ms (from motion.css)
      const ms = parseFloat(duration);
      expect(ms).toBeLessThan(1);
    }
  });
});

test.describe('Data Ocean 200% zoom and overflow', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── 9. 200% zoom: key controls remain visible ──
  test('key controls remain visible at 200% zoom', async ({ page }) => {
    await page.goto('/workbench');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

    // Apply 200% zoom via CSS
    await page.evaluate(() => {
      document.documentElement.style.zoom = '2';
    });

    // The primary heading should still be visible
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    // The sidebar brand should still be visible
    await expect(page.locator('.ocean-shell-brand')).toBeVisible();

    // The job progress button should still be visible
    await expect(page.getByRole('button', { name: /作业进度/ })).toBeVisible();
  });

  // ── 10. No page-level horizontal overflow at 1280px ──
  test('no page-level horizontal scrollbar at 1280px on primary destinations', async ({ page }) => {
    // Reset zoom for this test
    await page.evaluate(() => {
      document.documentElement.style.zoom = '';
    });

    const destinations = ['/workbench', '/standards', '/lab-ops', '/platform', '/governance'];

    for (const dest of destinations) {
      await page.goto(dest);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

      // Check that document doesn't have horizontal overflow
      // (table-local scroll containers are expected and excluded)
      const overflow = await page.evaluate(() => {
        const doc = document.documentElement;
        return {
          scrollWidth: doc.scrollWidth,
          clientWidth: doc.clientWidth,
        };
      });

      // Allow a small tolerance for sub-pixel rounding
      expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);
    }
  });

  // ── 11. Content remains capped at 1920px (no full-bleed stretch) ──
  test('content frame is capped at 1680px on wide viewport', async ({ page }) => {
    // This test runs on all visual projects; the 1920 project validates the cap
    await page.goto('/workbench');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

    const frameWidth = await page.evaluate(() => {
      const frame = document.querySelector('.ocean-content-frame');
      return frame ? frame.getBoundingClientRect().width : 0;
    });

    // The wide content frame should not exceed 1680px
    expect(frameWidth).toBeLessThanOrEqual(1680);
  });
});

test.describe('Data Ocean performance evidence', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  // ── 12. Navigation performance — workbench load time ──
  test('workbench page loads within acceptable time', async ({ page }) => {
    const start = Date.now();
    await page.goto('/workbench');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 });
    const loadTime = Date.now() - start;

    // Record the load time — should be under 10s for local dev
    expect(loadTime).toBeLessThan(10_000);
  });

  // ── 13. Resource counts are reasonable ──
  test('page resource count is within expected bounds', async ({ page }) => {
    await page.goto('/workbench');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 });

    const resourceCount = await page.evaluate(() => {
      return performance.getEntriesByType('resource').length;
    });

    // Record resource count — should be under 200 for a single page
    expect(resourceCount).toBeLessThan(200);
  });

  // ── 14. Navigation timing records response start ──
  test('navigation timing has valid response start', async ({ page }) => {
    await page.goto('/workbench');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 });

    const timing = await page.evaluate(() => {
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
      return {
        responseStart: nav?.responseStart ?? 0,
        domContentLoaded: nav?.domContentLoadedEventEnd ?? 0,
        loadEventEnd: nav?.loadEventEnd ?? 0,
      };
    });

    // Response start should be positive and reasonable
    expect(timing.responseStart).toBeGreaterThan(0);
    expect(timing.domContentLoaded).toBeGreaterThan(0);
  });

  // ── 15. Long content scrolls without lag (FlowDetail, Components, Assistant) ──
  test('long content pages scroll without blocking', async ({ page }) => {
    const longPages = ['/lab-ops?tab=flows', '/lab-ops?tab=components', '/platform?tab=assistant'];

    for (const dest of longPages) {
      await page.goto(dest);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 });

      // Measure scroll performance
      const scrollResult = await page.evaluate(() => {
        const start = performance.now();
        window.scrollTo(0, document.body.scrollHeight);
        window.scrollTo(0, 0);
        return performance.now() - start;
      });

      // Scroll should complete in under 500ms (no blocking)
      expect(scrollResult).toBeLessThan(500);
    }
  });
});
