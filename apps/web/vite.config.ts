/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';

// IRIP Web 控制台 Vite 配置
// - 开发代理：/api → 本地 FastAPI（8000 端口）
// - 单元测试：Vitest + jsdom
// - 构建优化：manualChunks 拆分大体积 vendor，chunkSizeWarningLimit 消除第三方库固有体积警告
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // 确保 Vite 正确处理 KaTeX 字体文件（woff2/woff/ttf）
  assetsInclude: ['**/*.woff2', '**/*.woff', '**/*.ttf'],
  build: {
    rollupOptions: {
      output: {
        // 将大体积第三方库拆成独立 chunk，减少主包体积
        manualChunks: {
          // React 核心
          'react-vendor': ['react', 'react-dom'],
          // Ant Design 组件库
          'antd-vendor': ['antd', '@ant-design/icons'],
          // TanStack Query/Router
          'tanstack-vendor': [
            '@tanstack/react-query',
            '@tanstack/react-router',
          ],
        },
      },
    },
    // chunk 体积警告阈值：plotly.js-dist-min 固有体积 ~4.6MB，已通过 dynamic import 独立 chunk
    chunkSizeWarningLimit: 5000,
  },
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
