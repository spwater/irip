import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Mock window.matchMedia for jsdom（Ant Design 组件依赖此 API）
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Mock window.scrollTo for jsdom（TanStack Router scroll-restoration 依赖此 API）
window.scrollTo = (() => {}) as typeof window.scrollTo;

// Mock window.getComputedStyle for jsdom（确保 Ant Design 组件能获取样式）
const originalGetComputedStyle = window.getComputedStyle;
window.getComputedStyle = (elt: Element, pseudoElt?: string | null): CSSStyleDeclaration => {
  return originalGetComputedStyle.call(window, elt, pseudoElt);
};

// 每个测试结束后清理 DOM 和 localStorage
afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
});
