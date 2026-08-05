import { defineConfig, devices } from '@playwright/test';

/**
 * IRIP Playwright E2E 配置。
 *
 * F-16: 修复 E2E 路径和默认 URL。
 * 测试文件位于项目根 tests/e2e/ 目录，
 * 基础 URL 指向 compose 编排的 web 端口 8080。
 *
 * 认证策略：
 *   后端使用 rotating refresh tokens（每个 refresh token 仅可用一次），
 *   无法通过 storageState 跨测试复用会话。
 *   已认证测试文件使用 test.describe.configure({ mode: 'serial' }) + 共享 BrowserContext，
 *   每个文件仅在 beforeAll 中登录一次，后续测试复用同一 context（cookie 自动更新）。
 *   v0-login 测试单独运行，测试登录流程本身。
 */
export default defineConfig({
  // 从 apps/web/ 目录需要向上两级到达项目根，再进入 tests/e2e/
  testDir: '../../tests/e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  use: {
    // F-16: 默认 URL 对应 compose web 端口 8080
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
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
