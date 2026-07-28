/**
 * IRIP E2E — Reusable authentication helper.
 *
 * Provides loginAsAdmin(page) that navigates to /login, fills the bootstrap
 * admin credentials, and waits for the workbench redirect. Credentials are
 * overridable via environment variables for CI or alternative environments.
 *
 * Usage:
 *   import { loginAsAdmin } from './helpers/auth';
 *   await loginAsAdmin(page);
 */

import type { Page } from '@playwright/test';

/** Default admin credentials (overridable via env vars). */
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? 'admin@irip.local';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin123';

/**
 * Log in as the bootstrap admin user and wait for the workbench redirect.
 *
 * @param page - Playwright Page instance
 * @returns Promise that resolves when the workbench URL is confirmed
 */
export async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto('/login');

  // Confirm the login page rendered before filling
  await page.getByText('IRIP 控制台').waitFor({ state: 'visible', timeout: 10_000 });

  await page.getByLabel('邮箱').fill(ADMIN_EMAIL);
  await page.getByLabel('密码').fill(ADMIN_PASSWORD);

  await page.getByRole('button', { name: /登\s*录/ }).click();

  // Wait for redirect to any authenticated route (workbench, standards, etc.)
  await page.waitForURL(/\/(workbench|standards|lab-ops|platform|governance)/, { timeout: 15_000 });
}

/**
 * Log in as a specific user with explicit credentials.
 *
 * @param page - Playwright Page instance
 * @param email - login email
 * @param password - login password
 * @returns Promise that resolves when an authenticated route is confirmed
 */
export async function loginAs(
  page: Page,
  email: string,
  password: string,
): Promise<void> {
  await page.goto('/login');
  await page.getByText('IRIP 控制台').waitFor({ state: 'visible', timeout: 10_000 });
  await page.getByLabel('邮箱').fill(email);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button', { name: /登\s*录/ }).click();
  await page.waitForURL(/\/(workbench|standards|lab-ops|platform|governance|facts|parameters)/, {
    timeout: 15_000,
  });
}
