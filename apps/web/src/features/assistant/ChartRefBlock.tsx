/**
 * 引用式图表渲染组件（chart-ref）。
 *
 * LLM 只输出轻量指令（用哪个样品的哪组 series、画什么类型），
 * 本组件从 systemContext（已加载的实验数据 JSON）中提取数据，
 * 构建 ECharts option 渲染。
 *
 * 指令格式：
 * ```chart-ref
 * {
 *   "sample": "BL-18.txt",     // 样品标签（匹配 system_context 里的 ### 样品: XXX）
 *   "series_index": 0,          // 第几组 series（默认 0）
 *   "chart_type": "line",       // line / bar / scatter
 *   "x_col": 0,                 // columns 数组索引（X 轴）
 *   "y_col": 1,                 // columns 数组索引（Y 轴）
 *   "title": "拉曼光谱",         // 图表标题（可选）
 *   "x_name": "拉曼位移",        // X 轴名称（可选，默认用 columns[x_col]）
 *   "y_name": "光谱强度"         // Y 轴名称（可选，默认用 columns[y_col]）
 * }
 * ```
 *
 * 多系列场景（如多样品对比）：
 * ```chart-ref
 * {
 *   "series": [
 *     {"sample":"BL-18.txt","series_index":0,"x_col":0,"y_col":1,"name":"BL-18"},
 *     {"sample":"BL-19.txt","series_index":0,"x_col":0,"y_col":1,"name":"BL-19"}
 *   ],
 *   "chart_type":"line",
 *   "title":"拉曼光谱对比",
 *   "x_name":"拉曼位移 (cm⁻¹)",
 *   "y_name":"光谱强度"
 * }
 * ```
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Spin, Typography } from 'antd';

const { Text } = Typography;

/** 单个数据系列定义 */
type SeriesSpec = {
  sample: string;
  series_index?: number;
  series_name?: string;
  x_col: number;
  y_col: number;
  name?: string;
};

/** chart-ref 指令 */
type ChartRefSpec = {
  series?: SeriesSpec[];
  sample?: string;
  series_index?: number;
  series_name?: string;
  x_col?: number;
  y_col?: number;
  chart_type?: string;
  title?: string;
  x_name?: string;
  y_name?: string;
};

/** 从 systemContext 解析出的样品数据 */
type SampleData = {
  label: string;
  metadata: Record<string, unknown>;
  points: unknown[];
  series: { name: string; columns: string[]; rows: unknown[][] }[];
};

/**
 * 从 systemContext 中提取所有样品的数据。
 *
 * systemContext 格式：
 * ```
 * 以下是实验数据，请基于此数据回答用户的问题：
 *
 * ### 样品: BL-18.txt
 * ```json
 * {"metadata":{...},"points":[...],"series":[{"name":"拉曼光谱","columns":[...],"rows":[...]}]}
 * ```
 *
 * ### 样品: BL-19.txt
 * ```json
 * ...
 * ```
 * ```
 */
function parseSamplesFromContext(systemContext: string | null | undefined): Map<string, SampleData> {
  const samples = new Map<string, SampleData>();
  if (!systemContext) return samples;

  // 按 "### 样品: XXX" 分割
  const blocks = systemContext.split(/### 样品:\s*/);
  const labelCount = new Map<string, number>();
  for (let i = 1; i < blocks.length; i++) {
    const block = blocks[i];
    // 标签是第一行（到换行或 ``` 之前）
    const labelMatch = block.match(/^([^\n`]+)/);
    if (!labelMatch) continue;
    const rawLabel = labelMatch[1].trim();

    // 提取 JSON 块
    const jsonMatch = block.match(/```json\n([\s\S]*?)```/);
    if (!jsonMatch) continue;

    try {
      const data = JSON.parse(jsonMatch[1]);
      // 同名样品用索引区分（如 "样品A" → "样品A" / "样品A (#2)" / "样品A (#3)"）
      const count = (labelCount.get(rawLabel) ?? 0) + 1;
      labelCount.set(rawLabel, count);
      const label = count > 1 ? `${rawLabel} (#${count})` : rawLabel;
      samples.set(label, {
        label,
        metadata: data.metadata ?? {},
        points: data.points ?? [],
        series: data.series ?? [],
      });
    } catch {
      // JSON 解析失败，跳过
    }
  }

  return samples;
}

/** 模糊匹配样品标签 */
function findSample(samples: Map<string, SampleData>, target: string): SampleData | undefined {
  // 精确匹配
  if (samples.has(target)) return samples.get(target);
  // 包含匹配
  for (const [key, val] of samples) {
    if (key.includes(target) || target.includes(key)) return val;
  }
  // 只有一个样品时直接返回
  if (samples.size === 1) return samples.values().next().value;
  return undefined;
}

export function ChartRefBlock({
  specStr,
  systemContext,
  sampleData,
}: {
  specStr: string;
  systemContext?: string | null;
  sampleData?: Array<{ label: string; data: Record<string, unknown> }> | null;
}): JSX.Element {
  const chartRef = useRef<HTMLDivElement>(null);
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
  const [error, setError] = useState<string | null>(null);

  // 解析指令
  const spec = useMemo<ChartRefSpec | null>(() => {
    try {
      return JSON.parse(specStr);
    } catch {
      return null;
    }
  }, [specStr]);

  // 从结构化 sampleData 或 systemContext 文本提取样品数据
  const samples = useMemo(() => {
    // 优先使用结构化数据（无文本解析风险）
    if (sampleData && sampleData.length > 0) {
      const map = new Map<string, SampleData>();
      const labelCount = new Map<string, number>();
      for (const s of sampleData) {
        const rawLabel = s.label;
        const count = (labelCount.get(rawLabel) ?? 0) + 1;
        labelCount.set(rawLabel, count);
        const label = count > 1 ? `${rawLabel} (#${count})` : rawLabel;
        const d = s.data;
        map.set(label, {
          label,
          metadata: (d.metadata as Record<string, unknown>) ?? {},
          points: (d.points as unknown[]) ?? [],
          series: (d.series as { name: string; columns: string[]; rows: unknown[][] }[]) ?? [],
        });
      }
      return map;
    }
    // 回退到文本解析（向后兼容旧接口）
    return parseSamplesFromContext(systemContext);
  }, [sampleData, systemContext]);

  // 构建 ECharts option
  const option = useMemo(() => {
    if (!spec) return null;

    // 构建系列列表
    const seriesList: SeriesSpec[] = spec.series ?? [
      {
        sample: spec.sample ?? '',
        series_index: spec.series_index,
        series_name: spec.series_name,
        x_col: spec.x_col ?? 0,
        y_col: spec.y_col ?? 1,
      },
    ];

    const chartType = spec.chart_type ?? 'line';
    const echartsSeries: Record<string, unknown>[] = [];
    let xData: (string | number)[] = [];
    let xAxisName = spec.x_name ?? '';
    let yAxisName = spec.y_name ?? '';

    for (const s of seriesList) {
      const sample = findSample(samples, s.sample);
      if (!sample) {
        setError(`未找到样品: ${s.sample}`);
        return null;
      }
      const seriesData = (() => {
        // 1. If series_name specified, find by name (fuzzy match)
        if (s.series_name) {
          const found = sample.series.find(
            (sr) => sr.name.includes(s.series_name!) || s.series_name!.includes(sr.name)
          );
          if (found) return found;
        }
        // 2. Use series_index if specified
        const idx = s.series_index ?? 0;
        const byIdx = sample.series[idx];
        if (byIdx) return byIdx;
        // 3. Fallback: find the series with the most rows (likely the main data)
        return sample.series.reduce((a, b) =>
          (b.rows?.length ?? 0) > (a.rows?.length ?? 0) ? b : a
        );
      })();
      if (!seriesData) {
        setError(`样品 ${s.sample} 无 series[${s.series_index ?? 0}]`);
        return null;
      }

      const xCol = s.x_col ?? 0;
      const yCol = s.y_col ?? 1;
      const data = seriesData.rows.map((row) => [row[xCol], row[yCol]]);

      if (data.length === 0) {
        setError(`样品 ${s.sample} 无数据行`);
        return null;
      }

      // 第一组 series 提供 X 轴标签
      if (echartsSeries.length === 0) {
        xData = seriesData.rows.map((row) => row[xCol] as string | number);
        if (!xAxisName) xAxisName = seriesData.columns[xCol] ?? '';
        if (!yAxisName) yAxisName = seriesData.columns[yCol] ?? '';
      }

      echartsSeries.push({
        type: chartType,
        name: s.name ?? sample.label,
        data: chartType === 'line' || chartType === 'bar' ? data.map((d) => d[1]) : data,
        showSymbol: data.length <= 50,
        smooth: false,
      });
    }

    setError(null);

    // 统一布局：title 在最顶部，legend 紧跟 title 下方，grid 在 legend 下方
    const hasLegend = echartsSeries.length > 1;
    const hasTitle = !!spec.title;

    return {
      title: spec.title ? { text: spec.title, left: 'center', top: 8 } : undefined,
      tooltip: { trigger: chartType === 'scatter' ? 'item' : 'axis' },
      legend: hasLegend ? { top: 36, left: 'center' } : undefined,
      grid: {
        left: '8%',
        right: '5%',
        top: hasTitle && hasLegend ? 70 : hasTitle ? 45 : hasLegend ? 50 : '10%',
        bottom: '8%',
        containLabel: true,
      },
      xAxis: {
        type: chartType === 'scatter' ? 'value' : 'category',
        name: xAxisName,
        nameLocation: 'middle',
        nameGap: 30,
        data: chartType === 'scatter' ? undefined : xData,
        axisLabel: {
          interval: 'auto',
          rotate: xData.length > 20 ? 45 : 0,
          fontSize: 10,
          hideOverlap: true,
        },
      },
      yAxis: {
        type: 'value',
        name: yAxisName,
        nameLocation: 'middle',
        nameGap: 40,
      },
      series: echartsSeries,
      ...((): { dataZoom?: unknown[] } => {
        // 大数据量启用缩放
        const totalPoints = echartsSeries.reduce((sum, s) => {
          const d = (s as { data?: unknown[] }).data;
          return sum + (Array.isArray(d) ? d.length : 0);
        }, 0);
        if (totalPoints > 50) {
          return {
            dataZoom: [
              { type: 'inside', xAxisIndex: 0 },
              { type: 'slider', xAxisIndex: 0, bottom: echartsSeries.length > 1 ? '15%' : '3%', height: 20 },
            ],
          };
        }
        return {};
      })(),
    };
  }, [spec, samples]);

  // 渲染 ECharts
  useEffect(() => {
    if (!option || !chartRef.current) return;

    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;

    const initChart = (echarts: typeof import('echarts')) => {
      if (cancelled || !chartRef.current) return;
      const w = chartRef.current.offsetWidth;
      if (w === 0) return false; // 容器还没布局好，等 ResizeObserver
      chartInstanceRef.current?.dispose();
      const chart = echarts.init(chartRef.current);
      chart.setOption(option);
      chartInstanceRef.current = chart;
      return true;
    };

    import('echarts').then((echarts) => {
      if (cancelled || !chartRef.current) return;
      if (initChart(echarts)) return; // 成功初始化

      // 容器宽度为 0，等 ResizeObserver 第一次回调
      resizeObserver = new ResizeObserver(() => {
        if (cancelled || !chartRef.current) return;
        if (initChart(echarts)) {
          resizeObserver?.disconnect();
          resizeObserver = new ResizeObserver(() => chartInstanceRef.current?.resize());
          resizeObserver.observe(chartRef.current);
        }
      });
      resizeObserver.observe(chartRef.current);
    });

    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      chartInstanceRef.current?.dispose();
      chartInstanceRef.current = null;
    };
  }, [option]);

  if (error) {
    return (
      <div style={{ padding: 16, textAlign: 'center' }}>
        <Text type="danger">{error}</Text>
      </div>
    );
  }

  if (!option) {
    return (
      <div style={{ padding: 16, textAlign: 'center' }}>
        <Spin tip="解析图表指令..." />
      </div>
    );
  }

  return (
    <div style={{ width: '100%', position: 'relative' }}>
      <button
        type="button"
        onClick={handleExportPNG}
        disabled={exporting}
        style={{
          position: 'absolute', right: 0, bottom: 0, zIndex: 10,
          background: 'rgba(255,255,255,0.9)', border: '1px solid #d9d9d9',
          borderRadius: 4, padding: '2px 8px', fontSize: 12,
          cursor: 'pointer', opacity: 0.7, transition: 'opacity 0.2s',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.opacity = '1')}
        onMouseLeave={(e) => (e.currentTarget.style.opacity = '0.7')}
      >
        {exporting ? '导出中...' : '导出PNG'}
      </button>
      <div ref={chartRef} style={{ width: '100%', height: 400 }} />
    </div>
  );
}

export default ChartRefBlock;
