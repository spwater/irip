import { defineConfig, devices } from '@playwright/test';

/**
 * IRIP Playwright E2E 配置。
 *
 * Phase 6 — Data Ocean Quality and Release:
 *   - chromium-functional: standard Chromium for all functional specs
 *     (ignores the visual spec so functional tests are not tripled).
 *   - visual-1280 / visual-1440 / visual-1920: three desktop viewports
 *     dedicated to data-ocean-visual.spec.ts screenshot baselines.
 *
 * F-16: 测试文件位于项目根 tests/e2e/ 目录，
 * 基础 URL 指向 compose 编排的 web 端口 8080。
 */

/** Viewport definitions for the three visual regression projects. */
const VISUAL_VIEWPORTS: Array<{ name: string; viewport: { width: number; height: number } }> = [
  { name: 'visual-1280', viewport: { width: 1280, height: 800 } },
  { name: 'visual-1440', viewport: { width: 1440, height: 900 } },
  { name: 'visual-1920', viewport: { width: 1920, height: 1080 } },
];

export default defineConfig({
  // 从 apps/web/ 目录需要向上两级到达项目根，再进入 tests/e2e/
  testDir: '../../tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    // F-16: 默认 URL 对应 compose web 端口 8080
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    trace: 'on-first-retry',
    locale: 'zh-CN',
  },
  projects: [
    // ── Standard functional Chromium project ──
    // Runs all specs except the visual regression spec to avoid tripling runs.
    {
      name: 'chromium-functional',
      testIgnore: /data-ocean-visual\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    // ── Three visual regression projects at desktop viewports ──
    // Each runs ONLY data-ocean-visual.spec.ts at its fixed viewport.
    ...VISUAL_VIEWPORTS.map(({ name, viewport }) => ({
      name,
      testMatch: /data-ocean-visual\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        viewport,
      },
    })),
  ],
});
