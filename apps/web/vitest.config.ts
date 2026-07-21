import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';

// IRIP Web 控制台 Vitest 配置
// 独立配置文件，与 vite.config.ts 保持一致，
// 确保 vitest run 优先使用此文件。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    css: false,
    testTimeout: 15000,
  },
});
