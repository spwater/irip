/**
 * ChartBlock — ECharts 图表块组件。
 *
 * 从 MessageThread.tsx 提取。
 *
 * H-14: ECharts data is parsed independently, not through Markdown rendering.
 * The option JSON is parsed safely with JSON.parse and rendered via echarts.
 *
 * L-03 整改：
 * - ECharts 实例保存到 ref，避免每次 render 重复创建
 * - useEffect 依赖数组收敛为 [parsed]
 * - cleanup 函数可靠调用 chart.dispose()，防止内存泄漏
 * - 使用 cancelled 标志处理异步竞态（import 完成前组件已卸载）
 * - 使用 ResizeObserver 监听容器尺寸变化，自动 resize 图表
 * - 长对话反复进入后实例数和内存稳定
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Typography } from 'antd';
import type { ChartBlockProps } from '../types';

const { Text } = Typography;

export function ChartBlock({ optionStr }: ChartBlockProps): JSX.Element {
  const chartRef = useRef<HTMLDivElement>(null);
  // L-03: 保存 ECharts 实例到 ref，cleanup 时 dispose
  const chartInstanceRef = useRef<{ dispose: () => void; resize: () => void; getDataURL: (opts?: Record<string, unknown>) => string } | null>(null);
  const [exporting, setExporting] = useState(false);

  const handleExportPNG = () => {
    if (!chartInstanceRef.current) return;
    setExporting(true);
    try {
      const dataURL = chartInstanceRef.current.getDataURL({
        type: 'png',
        pixelRatio: 3,
        backgroundColor: '#fff',
      });
      const link = document.createElement('a');
      link.href = dataURL;
      link.download = `chart_${Date.now()}.png`;
      link.click();
    } finally {
      setExporting(false);
    }
  };

  // 防抖：流式传输中不立即渲染，等内容稳定（300ms 无变化）后再渲染
  const [debouncedStr, setDebouncedStr] = useState(optionStr);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedStr(optionStr), 300);
    return () => clearTimeout(timer);
  }, [optionStr]);

  const parsed = useMemo(() => {
    // 1. 先尝试标准 JSON.parse
    try {
      return JSON.parse(debouncedStr);
    } catch {
      // fall through to lenient parser
    }
    // 2. 宽松解析：将 JS 对象语法转为合法 JSON
    //    - 无引号的 key: title: → "title":
    //    - 单引号字符串: 'xxx' → "xxx"
    //    - 尾逗号: ,} → }, ,,] → ]
    try {
      const lenient = debouncedStr
        .replace(/([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)/g, '$1"$2"$3')
        .replace(/'/g, '"')
        .replace(/,(\s*[}\]])/g, '$1');
      return JSON.parse(lenient);
    } catch {
      return null;
    }
  }, [debouncedStr]);

  useEffect(() => {
    if (!parsed || !chartRef.current) return;

    // L-03: 异步取消标志，防止 import 完成前组件已卸载时创建孤儿实例
    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;

    // Find parent container width
    let width = 500;
    let el: HTMLElement | null = chartRef.current.parentElement;
    while (el) {
      if (el.clientWidth > 0) {
        width = el.clientWidth - 32;
        break;
      }
      el = el.parentElement;
    }
    if (width < 200) width = 500;

    // Enhance option with safe defaults
    const safeOption = { ...parsed };
    if (!safeOption.grid) safeOption.grid = {};
    safeOption.grid.containLabel = true;
    if (safeOption.xAxis && !Array.isArray(safeOption.xAxis)) {
      safeOption.xAxis.nameLocation = 'middle';
      safeOption.xAxis.nameGap = 25;
    }
    // If both title and legend exist, push legend below title to avoid overlap
    if (safeOption.title && safeOption.legend) {
      const legend = { ...safeOption.legend };
      if (legend.top === undefined) {
        legend.top = 30;  // below the default title height
      }
      safeOption.legend = legend;
      // Also push grid down so it doesn't overlap with legend
      if (safeOption.grid.top === undefined) {
        safeOption.grid.top = 60;
      }
    }

    import('echarts').then((echarts) => {
      // L-03: 组件已卸载则不再创建实例
      if (cancelled || !chartRef.current) return;

      // L-03: 先 dispose 旧实例（如果有），防止重复创建
      if (chartInstanceRef.current) {
        chartInstanceRef.current.dispose();
        chartInstanceRef.current = null;
      }

      const chart = echarts.init(chartRef.current, undefined, { width, height: 400 });
      chart.setOption(safeOption);
      chartInstanceRef.current = chart;

      // L-03: ResizeObserver 监听容器尺寸变化
      if (chartRef.current && typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(() => {
          if (!cancelled && chartRef.current) {
            chart.resize();
          }
        });
        resizeObserver.observe(chartRef.current);
      }

      // Add copy button (L-01: 使用语义 button 元素，支持键盘)
      if (chartRef.current && !chartRef.current.querySelector('.echarts-copy-btn')) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'echarts-copy-btn';
        btn.textContent = '\u{1F4CB}';
        btn.title = 'Copy ECharts config';
        btn.setAttribute('aria-label', 'Copy ECharts config');
        btn.style.cssText =
          'position:absolute;top:8px;right:8px;width:28px;height:28px;' +
          'display:flex;align-items:center;justify-content:center;cursor:pointer;' +
          'background:rgba(232,246,249,0.9);border:1px solid rgba(24,102,133,0.20);' +
          'border-radius:4px;font-size:14px;z-index:100;opacity:0;transition:opacity 0.2s;' +
          'padding:0;';
        chartRef.current.onmouseenter = () => { btn.style.opacity = '1'; };
        chartRef.current.onmouseleave = () => { btn.style.opacity = '0'; };
        chartRef.current.onfocus = () => { btn.style.opacity = '1'; };
        chartRef.current.onblur = () => { btn.style.opacity = '0'; };
        btn.onclick = (e: MouseEvent) => {
          e.stopPropagation();
          navigator.clipboard.writeText(JSON.stringify(safeOption, null, 2)).then(() => {
            btn.textContent = '\u2713';
            btn.title = 'Copied';
            setTimeout(() => {
              btn.textContent = '\u{1F4CB}';
              btn.title = 'Copy ECharts config';
            }, 1500);
          });
        };
        chartRef.current.appendChild(btn);
      }
    });

    return () => {
      // L-03: 标记取消，防止异步 import 完成后创建孤儿实例
      cancelled = true;
      // L-03: 断开 ResizeObserver
      if (resizeObserver) {
        resizeObserver.disconnect();
        resizeObserver = null;
      }
      // L-03: dispose ECharts 实例，释放内存
      if (chartInstanceRef.current) {
        chartInstanceRef.current.dispose();
        chartInstanceRef.current = null;
      }
    };
  }, [parsed]);

  if (!parsed) {
    // 流式传输中：JSON 可能还没传完，不显示 error 也不占高度
    const looksIncomplete = debouncedStr.trim().length > 0 && !debouncedStr.trim().endsWith('}');
    if (looksIncomplete) {
      return <div style={{ width: '100%', minHeight: 40, margin: '8px 0' }} />;
    }
    return <Text type="danger">Chart config parse error</Text>;
  }

  return (
    <div style={{ width: '100%', margin: '8px 0', position: 'relative' }}>
      <button
        type="button"
        onClick={handleExportPNG}
        disabled={exporting}
        style={{
          position: 'absolute', right: 0, top: -4, zIndex: 10,
          background: 'rgba(255,255,255,0.9)', border: '1px solid #d9d9d9',
          borderRadius: 4, padding: '2px 8px', fontSize: 12,
          cursor: 'pointer', opacity: 0.7, transition: 'opacity 0.2s',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.opacity = '1')}
        onMouseLeave={(e) => (e.currentTarget.style.opacity = '0.7')}
      >
        {exporting ? '导出中...' : '导出PNG'}
      </button>
      <div
        ref={chartRef}
        style={{ width: '100%', height: 400 }}
      />
    </div>
  );
}

export default ChartBlock;
