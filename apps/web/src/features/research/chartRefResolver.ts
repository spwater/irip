/**
 * chart-ref 指令 → 完整 ECharts option 解析工具。
 *
 * 从 ChartRefBlock.tsx 提取的纯函数，用于在推送结论栏时将轻量 chart-ref
 * 指令解析为含完整 series 数据的 ECharts option，锁定为 content_snapshot。
 *
 * 解析后不再依赖 fact_samples，底层数据变化不影响已推送条目。
 */

/** 单个数据系列定义 */
export type SeriesSpec = {
  sample: string;
  series_index?: number;
  series_name?: string;
  x_col: number;
  y_col: number;
  name?: string;
};

/** chart-ref 指令 */
export type ChartRefSpec = {
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

/** 从系统上下文解析出的样品数据 */
export type SampleData = {
  label: string;
  metadata: Record<string, unknown>;
  points: unknown[];
  series: { name: string; columns: string[]; rows: unknown[][] }[];
};

/** fact_samples 条目结构 */
export type FactSample = { label: string; data: Record<string, unknown> };

/** 解析结果 */
export type ResolveResult = {
  /** 完整 ECharts option（失败时为 null） */
  option: Record<string, unknown> | null;
  /** 解析错误信息（失败时非 null） */
  error: string | null;
};

/**
 * 从结构化 sampleData 数组构建样品映射。
 * 同名样品用索引区分（如 "样品A" → "样品A" / "样品A (#2)"）。
 */
export function buildSampleMap(
  sampleData: FactSample[] | null | undefined,
): Map<string, SampleData> {
  const map = new Map<string, SampleData>();
  if (!sampleData || sampleData.length === 0) return map;
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
      series:
        (d.series as { name: string; columns: string[]; rows: unknown[][] }[]) ??
        [],
    });
  }
  return map;
}

/** 模糊匹配样品标签 */
export function findSample(
  samples: Map<string, SampleData>,
  target: string,
): SampleData | undefined {
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

/**
 * 将 chart-ref 指令字符串解析为完整 ECharts option（含 series 数据）。
 *
 * @param specStr chart-ref 指令 JSON 字符串
 * @param sampleData 已加载的样品数据（fact_samples 结构）
 * @returns { option, error }：成功时 option 非空、error 为 null
 */
export function resolveChartRefOption(
  specStr: string,
  sampleData: FactSample[] | null | undefined,
): ResolveResult {
  let spec: ChartRefSpec | null = null;
  try {
    spec = JSON.parse(specStr) as ChartRefSpec;
  } catch {
    return { option: null, error: 'chart-ref 指令解析失败' };
  }
  if (!spec) return { option: null, error: 'chart-ref 指令为空' };

  const samples = buildSampleMap(sampleData);

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
      return { option: null, error: `未找到样品: ${s.sample}` };
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
      // 3. Fallback: find the series with the most rows
      return sample.series.reduce((a, b) =>
        (b.rows?.length ?? 0) > (a.rows?.length ?? 0) ? b : a
      );
    })();
    if (!seriesData) {
      return {
        option: null,
        error: `样品 ${s.sample} 无 series[${s.series_index ?? 0}]`,
      };
    }

    const xCol = s.x_col ?? 0;
    const yCol = s.y_col ?? 1;
    const data = seriesData.rows.map((row) => [row[xCol], row[yCol]]);

    if (data.length === 0) {
      return { option: null, error: `样品 ${s.sample} 无数据行` };
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
      data:
        chartType === 'line' || chartType === 'bar'
          ? data.map((d) => d[1])
          : data,
      showSymbol: data.length <= 50,
      smooth: false,
    });
  }

  const option: Record<string, unknown> = {
    title: spec.title ? { text: spec.title, left: 'center' } : undefined,
    tooltip: { trigger: chartType === 'scatter' ? 'item' : 'axis' },
    legend: echartsSeries.length > 1 ? { bottom: 0 } : undefined,
    grid: {
      left: '8%',
      right: '5%',
      bottom: echartsSeries.length > 1 ? '15%' : '12%',
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
            {
              type: 'slider',
              xAxisIndex: 0,
              bottom: echartsSeries.length > 1 ? '15%' : '3%',
              height: 20,
            },
          ],
        };
      }
      return {};
    })(),
  };

  return { option, error: null };
}

export default resolveChartRefOption;
