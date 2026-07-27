/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';

// IRIP Web 控制台 Vite 配置
// - 开发代理：/api → 本地 FastAPI（8000 端口）
// - 单元测试：Vitest + jsdom
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // 确保 Vite 正确处理 KaTeX 字体文件（woff2/woff/ttf）
  assetsInclude: ['**/*.woff2', '**/*.woff', '**/*.ttf'],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    css: false,
  },
});
