/**
 * Data Ocean Phase 6 — Navigation E2E
 *
 * Verifies the five primary navigation destinations retain their routes and
 * selected-navigation semantics after the Data Ocean UI upgrade.
 *
 * The sidebar menu in AppShell.tsx uses Ant Design Menu with role="menu"
 * wrapping (nav aria-label="主导航").  Each menu item key maps to a route.
 * After clicking, the URL must match and the menu item must reflect
 * aria-selected="true".
 */

import { test, expect } from '@playwright/test';
import { loginAsAdmin } from './helpers/auth';

/**
 * Five primary destinations — [menu label, route path, h1 heading].
 * These mirror the NAV_ITEMS in AppShell.tsx.
 */
const DESTINATIONS: ReadonlyArray<readonly [string, string, string]> = [
  ['研发看板', '/workbench', '研发看板'],
  ['实验室建设', '/standards', '实验室建设'],
  ['实验室运营', '/lab-ops', '实验室运营'],
  ['平台应用', '/platform', '平台应用'],
  ['平台治理', '/governance', '平台治理'],
];

test.describe('Data Ocean primary navigation', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('five primary destinations retain route and selected navigation semantics', async ({ page }) => {
    for (const [label, path, heading] of DESTINATIONS) {
      // Click the menu item by role and accessible name
      await page.getByRole('menuitem', { name: label }).click();

      // URL must match the expected path
      await expect(page).toHaveURL(new RegExp(path));

      // The page heading (h1 / PageIntro title) must be visible
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible({
        timeout: 10_000,
      });

      // The clicked menu item must reflect aria-selected="true"
      await expect(page.getByRole('menuitem', { name: label })).toHaveAttribute(
        'aria-selected',
        'true',
      );
    }
  });

  test('navigation preserves authentication across all destinations', async ({ page }) => {
    for (const [label, path] of DESTINATIONS) {
      await page.getByRole('menuitem', { name: label }).click();
      await expect(page).toHaveURL(new RegExp(path));

      // The login page should never appear — we must stay authenticated
      await expect(page).not.toHaveURL(/\/login/);

      // The user avatar in the header should remain visible
      await expect(page.locator('.ocean-shell-avatar')).toBeVisible();
    }
  });

  test('deep link to each destination works after login', async ({ page, baseURL }) => {
    for (const [, path] of DESTINATIONS) {
      await page.goto(path);
      // Should not redirect back to login
      await expect(page).not.toHaveURL(/\/login/);
      await expect(page).toHaveURL(new RegExp(path));
    }
  });
});
