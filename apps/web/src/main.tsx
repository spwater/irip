import React from 'react';
import ReactDOM from 'react-dom/client';
import { RouterProvider } from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { router } from '@/app/router';
import 'katex/dist/katex.min.css';

/** TanStack Query 客户端实例 */
const queryClient = new QueryClient();

/**
 * IRIP Web 控制台入口
 * - ConfigProvider: 中文 locale
 * - AntApp: 提供 message/notification 上下文
 * - QueryClientProvider: TanStack Query 数据获取
 * - RouterProvider: TanStack Router
 */
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
