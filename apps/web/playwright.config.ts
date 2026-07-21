import { defineConfig, devices } from '@playwright/test';

/**
 * IRIP Playwright E2E 配置。
 *
 * 测试文件位于项目根 tests/e2e/ 目录，
 * 基础 URL 指向 Vite 开发服务器或 nginx 生产服务。
 */
export default defineConfig({
  testDir: '../tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'on-first-retry',
    locale: 'zh-CN',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
