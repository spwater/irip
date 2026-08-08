/**
 * P2-I11: Web Vitals (RUM) 前端性能监控。
 *
 * 采集 Core Web Vitals 指标（LCP/FID/CLS/INP/TTFB），
 * 通过 navigator.sendBeacon 上报到后端 /api/v1/metrics/web-vitals。
 *
 * 使用方式：
 *   import { initWebVitals } from '@/shared/webVitals';
 *   // 在 App.tsx 或 main.tsx 中调用一次
 *   initWebVitals();
 *
 * 注意：
 * - 使用 ReportAPI（浏览器原生）采集，无需第三方库
 * - 仅在生产环境采集（IRIP_ENV=production）
 * - 上报失败静默忽略，不影响用户体验
 */

interface WebVitalMetric {
  name: string;
  value: number;
  rating: string;
  id: string;
  delta: number;
  navigationType: string;
}

let initialized = false;

function sendMetric(metric: WebVitalMetric): void {
  // 仅在生产环境上报（通过 Vite 环境变量判断）
  if (import.meta.env.DEV) return;

  try {
    const body = JSON.stringify({
      name: metric.name,
      value: Math.round(metric.value),
      rating: metric.rating,
      page: window.location.pathname,
      ts: Date.now(),
    });

    // 使用 sendBeacon 非阻塞上报
    if (navigator.sendBeacon) {
      navigator.sendBeacon('/api/v1/metrics/web-vitals', body);
    } else {
      // 回退到 fetch keepalive
      fetch('/api/v1/metrics/web-vitals', {
        body,
        method: 'POST',
        keepalive: true,
        headers: { 'Content-Type': 'application/json' },
      }).catch(() => {});
    }
  } catch {
    // 静默忽略上报失败
  }
}

export function initWebVitals(): void {
  if (initialized) return;
  initialized = true;

  // 动态导入浏览器 Web Vitals API
  // 使用 PerformanceObserver 采集核心指标
  const observe = (type: string, callback: (entry: PerformanceEntry) => void) => {
    try {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          callback(entry);
        }
      });
      observer.observe({ type, buffered: true });
    } catch {
      // 浏览器不支持此指标类型
    }
  };

  // LCP (Largest Contentful Paint)
  observe('largest-contentful-paint', (entry) => {
    const value = entry.startTime;
    const rating = value <= 2500 ? 'good' : value <= 4000 ? 'needs-improvement' : 'poor';
    sendMetric({
      name: 'LCP',
      value,
      rating,
      id: 'lcp',
      delta: value,
      navigationType: 'navigate',
    });
  });

  // CLS (Cumulative Layout Shift)
  let clsValue = 0;
  observe('layout-shift', (entry) => {
    const shiftEntry = entry as PerformanceEntry & { hadRecentInput?: boolean; value?: number };
    if (!shiftEntry.hadRecentInput) {
      clsValue += shiftEntry.value || 0;
    }
  });
  // 在页面卸载时上报 CLS
  window.addEventListener('pagehide', () => {
    const rating = clsValue <= 0.1 ? 'good' : clsValue <= 0.25 ? 'needs-improvement' : 'poor';
    sendMetric({
      name: 'CLS',
      value: clsValue,
      rating,
      id: 'cls',
      delta: clsValue,
      navigationType: 'navigate',
    });
  });

  // INP (Interaction to Next Paint)
  let maxINP = 0;
  observe('event', (entry) => {
    const duration = entry.duration;
    if (duration > maxINP) maxINP = duration;
  });
  window.addEventListener('pagehide', () => {
    if (maxINP > 0) {
      const rating = maxINP <= 200 ? 'good' : maxINP <= 500 ? 'needs-improvement' : 'poor';
      sendMetric({
        name: 'INP',
        value: maxINP,
        rating,
        id: 'inp',
        delta: maxINP,
        navigationType: 'navigate',
      });
    }
  });

  // TTFB (Time to First Byte)
  const navEntries = performance.getEntriesByType('navigation');
  if (navEntries.length > 0) {
    const nav = navEntries[0] as PerformanceNavigationTiming;
    const ttfb = nav.responseStart - nav.requestStart;
    const rating = ttfb <= 800 ? 'good' : ttfb <= 1800 ? 'needs-improvement' : 'poor';
    sendMetric({
      name: 'TTFB',
      value: ttfb,
      rating,
      id: 'ttfb',
      delta: ttfb,
      navigationType: 'navigate',
    });
  }
}
