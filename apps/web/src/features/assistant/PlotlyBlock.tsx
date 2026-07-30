/**
 * Plotly 图表渲染组件。
 *
 * 使用 react-plotly.js + plotly.js-dist-min 渲染 ```plotly 代码块中的 JSON 配置。
 * 动态 import 按需加载，避免影响初始包体积。
 *
 * 复用于：
 * - 消息区全尺寸图表（height 默认 400）
 * - 橱窗卡片缩略图（height 120）
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Typography } from 'antd';

const { Text } = Typography;

/** Plotly 组件类型（动态加载后赋值） */
type PlotlyComponentType = React.ComponentType<Record<string, unknown>>;

/** 模块级缓存：动态加载的 Plotly React 组件（仅加载一次） */
let plotlyComponentPromise: Promise<PlotlyComponentType> | null = null;

/**
 * 动态加载 react-plotly.js factory + plotly.js-dist-min。
 * 使用模块级缓存确保只加载一次。
 *
 * 关键：不能用 react-plotly.js 的默认导出（index.js），
 * 因为它内部 require('plotly.js/dist/plotly') 会加载完整 plotly.js（~3MB），
 * 与我们已加载的 plotly.js-dist-min 冲突，导致 "Cannot call a class as a function"。
 * 正确做法：用 factory 模式，传入我们自己加载的 plotly.js-dist-min 实例。
 */
async function loadPlotlyComponent(): Promise<PlotlyComponentType> {
  if (plotlyComponentPromise) {
    return plotlyComponentPromise;
  }
  plotlyComponentPromise = (async () => {
    // 先加载 plotly.js-dist-min
    const Plotly = await import('plotly.js-dist-min');
    // 用 factory 模式创建组件，传入我们的 Plotly 实例
    const factoryMod = await import('react-plotly.js/factory');
    const factory = (factoryMod as unknown as { default: (p: unknown) => PlotlyComponentType }).default;
    return factory(Plotly);
  })();
  return plotlyComponentPromise;
}

/**
 * 宽松 JSON 解析（复用 ChartBlock 的逻辑）。
 * 将 JS 对象语法转为合法 JSON：无引号 key、单引号、尾逗号。
 */
function lenientParse(str: string): Record<string, unknown> | null {
  // 1. 标准 JSON.parse
  try {
    return JSON.parse(str) as Record<string, unknown>;
  } catch {
    // fall through
  }
  // 2. 宽松解析
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
  const [PlotlyComp, setPlotlyComp] = useState<PlotlyComponentType | null>(null);
  const [loadError, setLoadError] = useState(false);

  // 防抖：流式传输中等内容稳定后再渲染
  const [debouncedStr, setDebouncedStr] = useState(optionStr);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedStr(optionStr), 300);
    return () => clearTimeout(timer);
  }, [optionStr]);

  // 解析 JSON 配置
  const parsed = useMemo(() => lenientParse(debouncedStr), [debouncedStr]);

  // 动态加载 Plotly 组件
  useEffect(() => {
    let cancelled = false;
    loadPlotlyComponent()
      .then((comp) => {
        if (!cancelled) {
          setPlotlyComp(comp);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLoadError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
    // 流式传输中：JSON 可能还没传完
    const looksIncomplete = debouncedStr.trim().length > 0 && !debouncedStr.trim().endsWith('}');
    if (looksIncomplete) {
      return <div style={{ width: '100%', minHeight: 40, margin: '8px 0' }} />;
    }
    return <Text type="danger">Plotly 配置解析失败</Text>;
  }

  if (loadError) {
    return <Text type="danger">Plotly 组件加载失败</Text>;
  }

  if (!PlotlyComp) {
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

  // react-plotly.js 的 Plot 组件接受 data、layout、config 等 props
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

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height, margin: '8px 0', position: 'relative' }}
    >
      <PlotlyComp
        data={data}
        layout={layout}
        config={config}
        style={{ width: '100%', height: '100%' }}
        useResizeHandler
      />
    </div>
  );
}

export default PlotlyBlock;
