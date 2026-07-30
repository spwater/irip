/**
 * 会话状态原子清理模块（H-15 + M-04 共享）
 *
 * 提供统一的 clearSessionState() 函数，在登出、refresh 失败、账号切换时
 * 原子清理 Query 缓存、Zustand store 和用户级 localStorage，防止跨账号残留。
 *
 * 设计要点：
 * - 本模块不导入任何 store 或 client，避免循环依赖
 * - QueryClient 和清理回调通过 register* 函数注入
 * - client.ts 的 401 拦截器和 AuthProvider 均可调用 clearSessionState()
 */

import type { QueryClient } from '@tanstack/react-query';

/** 清理回调类型 */
type CleanupCallback = () => void;

/** 注入的 QueryClient 引用 */
let queryClientRef: QueryClient | null = null;

/** 已注册的清理回调列表（Zustand store reset 等） */
const cleanupCallbacks: CleanupCallback[] = [];

/**
 * 注册 QueryClient 实例，供 clearSessionState 使用。
 * 应在应用入口（main.tsx）创建 QueryClient 后立即调用。
 */
export function registerQueryClient(client: QueryClient): void {
  queryClientRef = client;
}

/**
 * 注册额外的清理回调。
 * Zustand store 应在模块加载时注册自身的 reset 回调。
 */
export function registerCleanupCallback(cb: CleanupCallback): void {
  cleanupCallbacks.push(cb);
}

/**
 * 原子清理会话状态：Query 缓存 + 已注册清理回调 + 用户级 localStorage。
 *
 * 登出、refresh 失败、账号切换时调用，确保跨账号无残留。
 *
 * @param scope 可选，指定清理的 tenant+user 范围；未提供时清除所有 irip: 前缀
 */
export function clearSessionState(scope?: { tenant: string; user: string }): void {
  // 1. 清 Query 缓存（所有缓存的查询数据）
  if (queryClientRef) {
    queryClientRef.clear();
  }

  // 2. 执行已注册的清理回调（Zustand store reset 等）
  for (const cb of cleanupCallbacks) {
    try {
      cb();
    } catch {
      // 清理回调失败不应阻断后续清理
    }
  }

  // 3. 清用户级 localStorage
  try {
    if (scope) {
      // 指定 scope：只清除该 tenant+user 的 key
      const prefix = `irip:${scope.tenant}:${scope.user}`;
      Object.keys(localStorage)
        .filter((k) => k.startsWith(prefix))
        .forEach((k) => localStorage.removeItem(k));
    } else {
      // 未指定 scope：清除所有 irip: 前缀的 key（登出/refresh 失败场景）
      Object.keys(localStorage)
        .filter((k) => k.startsWith('irip:'))
        .forEach((k) => localStorage.removeItem(k));
    }
  } catch {
    // localStorage 不可用时忽略
  }
}
