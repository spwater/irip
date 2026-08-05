/**
 * useContainerHeight — 动态计算可用高度的 hook。
 *
 * 从 AssistantPage.tsx 提取。计算 100vh - header - content padding，
 * 避免硬编码 180px 在 header 高度变化时不准。
 */

import { useLayoutEffect, useRef, useState } from 'react';

export function useContainerHeight(): {
  containerRef: React.RefObject<HTMLDivElement>;
  containerHeight: string;
} {
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerHeight, setContainerHeight] = useState('calc(100vh - 180px)');

  useLayoutEffect(() => {
    const updateHeight = () => {
      const el = containerRef.current;
      if (!el) return;
      // 找到最近的 scrollable 祖先（Content 区域）
      const rect = el.getBoundingClientRect();
      const available = window.innerHeight - rect.top - 24; // 24px = ContentFrame padding-bottom
      setContainerHeight(`${available}px`);
    };
    updateHeight();
    window.addEventListener('resize', updateHeight);
    // 延迟一次，等 header 渲染完成（用 rAF 比 setTimeout 更早）
    const rafId = requestAnimationFrame(updateHeight);
    const timer = setTimeout(updateHeight, 100);
    return () => {
      window.removeEventListener('resize', updateHeight);
      cancelAnimationFrame(rafId);
      clearTimeout(timer);
    };
  }, []);

  return { containerRef, containerHeight };
}
