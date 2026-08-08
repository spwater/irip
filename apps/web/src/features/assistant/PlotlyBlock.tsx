/**
 * Plotly 图表渲染组件。
 *
 * 直接使用 plotly.js-basic-dist-min 的 Plotly.newPlot() API 渲染，
 * 不用 react-plotly.js（其 Babel CommonJS 包装与 Vite ESM React 冲突，
 * 导致 "Cannot call a class as a function"）。
 *
 * 使用 basic 版本（scatter/bar/pie/heatmap 等基础图表 ~1MB）替代完整版（~4.8MB），
 * 减少按需加载体积 77%。工业研究场景不需要 3D surface / geo maps / 金融图表等高级模块。
 *
 * 动态 import 按需加载，避免影响初始包体积。
 * 复用于：消息区全尺寸图表（height 400）+ 橱窗缩略图（height 120）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Typography } from 'antd';

const { Text } = Typography;

/** Plotly 模块类型（动态加载后使用） */
type PlotlyModule = {
  newPlot: (el: HTMLElement, data: unknown[], layout: Record<string, unknown>, config: Record<string, unknown>) => Promise<unknown>;
  react: (el: HTMLElement, data: unknown[], layout: Record<string, unknown>, config: Record<string, unknown>) => Promise<unknown>;
  purge: (el: HTMLElement) => void;
};

/** 模块级缓存：动态加载的 Plotly 模块（仅加载一次） */
let plotlyPromise: Promise<PlotlyModule> | null = null;

/**
 * 动态加载 plotly.js-basic-dist-min。
 * 使用模块级缓存确保只加载一次。
 */
async function loadPlotly(): Promise<PlotlyModule> {
  if (plotlyPromise) return plotlyPromise;
  plotlyPromise = import('plotly.js-basic-dist-min').then((m) => m as unknown as PlotlyModule);
  return plotlyPromise;
}

/**
 * 宽松 JSON 解析（复用 ChartBlock 的逻辑）。
 */
function lenientParse(str: string): Record<string, unknown> | null {
  try {
    return JSON.parse(str) as Record<string, unknown>;
  } catch {
    // fall through
  }
  try {
    const lenient = str
      .replace(/([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)/g, '$1"$2"$3')
      .replace(/'/g, '"')
      .replace(/,(\s*[}\]])/g, '$1');
    return JSON.parse(lenient) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function PlotlyBlock({
  optionStr,
  height = 400,
}: {
  /** Plotly JSON 配置字符串 */
  optionStr: string;
  /** 图表高度（全尺寸 400，缩略图 120） */
  height?: number;
}): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const plotlyRef = useRef<PlotlyModule | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);

  // 防抖：流式传输中等内容稳定后再渲染
  const [debouncedStr, setDebouncedStr] = useState(optionStr);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedStr(optionStr), 300);
    return () => clearTimeout(timer);
  }, [optionStr]);

  // 解析 JSON 配置
  const parsed = useMemo(() => lenientParse(debouncedStr), [debouncedStr]);

  // 动态加载 Plotly 模块
  useEffect(() => {
    let cancelled = false;
    loadPlotly()
      .then((mod) => {
        if (!cancelled) {
          plotlyRef.current = mod;
          setLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 渲染 / 更新图表
  useEffect(() => {
    if (!parsed || !loaded || !plotlyRef.current || !containerRef.current) return;

    const Plotly = plotlyRef.current;
    const el = containerRef.current;
    let cancelled = false;

    const data = (parsed.data ?? []) as unknown[];
    const rawLayout = (parsed.layout ?? {}) as Record<string, unknown>;
    const config = {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    };

    // 缩略图模式（height <= 120）：注入小字体 + 紧凑 margin + 隐藏 legend
    const isThumbnail = height <= 120;
    const layout = isThumbnail
      ? {
          ...rawLayout,
          font: { ...(rawLayout.font as Record<string, unknown> | undefined), size: 8 },
          margin: { l: 28, r: 8, t: 15, b: 20 },
          showlegend: false,
        }
      : rawLayout;

    // 首次用 newPlot，后续用 react 更新
    const hasExisting = el.querySelector('.plotly') !== null;
    if (hasExisting) {
      Plotly.react(el, data, layout, config).catch(() => {});
    } else {
      Plotly.newPlot(el, data, layout, config).catch(() => {});
    }

    return () => {
      if (!cancelled && el.querySelector('.plotly')) {
        Plotly.purge(el);
      }
    };
  }, [parsed, loaded]);

  // 复制配置按钮
  useEffect(() => {
    if (!parsed || !containerRef.current) return;
    const container = containerRef.current;
    if (container.querySelector('.plotly-copy-btn')) return;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'plotly-copy-btn';
    btn.textContent = '\u{1F4CB}';
    btn.title = '复制 Plotly 配置';
    btn.setAttribute('aria-label', '复制 Plotly 配置');
    btn.style.cssText =
      'position:absolute;top:8px;right:8px;width:28px;height:28px;' +
      'display:flex;align-items:center;justify-content:center;cursor:pointer;' +
      'background:rgba(232,246,249,0.9);border:1px solid rgba(24,102,133,0.20);' +
      'border-radius:4px;font-size:14px;z-index:100;opacity:0;transition:opacity 0.2s;padding:0;';
    container.onmouseenter = () => { btn.style.opacity = '1'; };
    container.onmouseleave = () => { btn.style.opacity = '0'; };
    btn.onclick = (e: MouseEvent) => {
      e.stopPropagation();
      navigator.clipboard.writeText(JSON.stringify(parsed, null, 2)).then(() => {
        btn.textContent = '\u2713';
        btn.title = '已复制';
        setTimeout(() => {
          btn.textContent = '\u{1F4CB}';
          btn.title = '复制 Plotly 配置';
        }, 1500);
      });
    };
    container.appendChild(btn);
  }, [parsed]);

  if (!parsed) {
    const looksIncomplete = debouncedStr.trim().length > 0 && !debouncedStr.trim().endsWith('}');
    if (looksIncomplete) {
      return <div style={{ width: '100%', minHeight: 40, margin: '8px 0' }} />;
    }
    return <Text type="danger">Plotly 配置解析失败</Text>;
  }

  if (loadError) {
    return <Text type="danger">Plotly 加载失败</Text>;
  }

  if (!loaded) {
    return (
      <div
        style={{
          width: '100%',
          height,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Text type="secondary">Plotly 加载中...</Text>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height, margin: '8px 0', position: 'relative' }}
    />
  );
}

export default PlotlyBlock;
