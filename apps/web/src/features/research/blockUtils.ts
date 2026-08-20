/**
 * 区块类型判定 + content_snapshot 构建工具。
 *
 * 用于 ReportBlockWrapper 在推送结论栏时，根据区块类型将原始 code 字符串
 * 构建为锁定后的 content_snapshot（dict），存库后不再依赖 fact_samples。
 */

import { resolveChartRefOption, type FactSample } from './chartRefResolver';

/** 归一化后的区块类型 */
export type BlockType = 'echarts' | 'chart_ref' | 'structured' | 'table' | 'text';

/**
 * 根据代码块语言判定归一化的区块类型。
 *
 * chart-ref / chart → chart_ref
 * echarts / describe_series → echarts
 * data / json → structured
 * 其它 → text
 */
export function detectBlockType(lang: string): BlockType {
  const normalized = (lang || '').toLowerCase();
  if (normalized === 'chart-ref' || normalized === 'chart') return 'chart_ref';
  if (
    normalized === 'echarts' ||
    normalized === 'describe_series' ||
    normalized === 'describe-series' ||
    normalized === 'describeseries'
  ) {
    return 'echarts';
  }
  if (normalized === 'data' || normalized === 'json') return 'structured';
  return 'text';
}

/**
 * 宽松解析 ECharts option JSON 字符串。
 *
 * 先尝试标准 JSON.parse；失败时将无引号 key、单引号、尾逗号修复后再解析。
 * 与 ChartBlock 的宽松解析逻辑一致。
 */
export function parseEchartsOption(codeStr: string): Record<string, unknown> | null {
  // 1. 先尝试标准 JSON.parse
  try {
    return JSON.parse(codeStr) as Record<string, unknown>;
  } catch {
    // fall through to lenient parser
  }
  // 2. 宽松解析：无引号 key / 单引号 / 尾逗号
  try {
    const lenient = codeStr
      .replace(/([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)/g, '$1"$2"$3')
      .replace(/'/g, '"')
      .replace(/,(\s*[}\]])/g, '$1');
    return JSON.parse(lenient) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * 构建 describe_series 指令的 ECharts option。
 *
 * describe_series 输出 [{name, data}] 结构，转为折线图 option。
 */
function buildDescribeSeriesOption(
  codeStr: string,
): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(codeStr);
    const rawData = Array.isArray(parsed) ? parsed[0] : parsed;
    if (rawData && Array.isArray(rawData.data)) {
      const name = typeof rawData.name === 'string' ? rawData.name : '数据序列';
      const data = rawData.data.map((v: unknown) =>
        typeof v === 'number' ? v : Number(v),
      );
      return {
        title: { text: name, left: 'center' },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'category',
          data: data.map((_: number, i: number) => i + 1),
          name: '序号',
        },
        yAxis: { type: 'value' },
        series: [{ name, type: 'line', data, smooth: true }],
      };
    }
  } catch {
    return null;
  }
  return null;
}

/**
 * 根据区块类型构建 content_snapshot（dict）。
 *
 * - echarts → 宽松解析 option JSON（describe_series 单独处理）
 * - chart_ref → 调 resolveChartRefOption() 返回完整 option
 * - structured/data/json → JSON.parse
 * - table → { text: codeStr }（实际 table 快照由 buildTableSnapshot 构建）
 * - text → { text: codeStr }
 *
 * @returns content_snapshot dict，解析失败时回退为 { text: codeStr }
 */
export function buildContentSnapshot(
  blockType: BlockType,
  codeStr: string,
  sampleData?: FactSample[] | null,
  lang?: string,
): Record<string, unknown> {
  const trimmed = codeStr.replace(/\n$/, '');

  if (blockType === 'chart_ref') {
    const { option } = resolveChartRefOption(trimmed, sampleData);
    if (option) return option;
    return { text: trimmed };
  }

  if (blockType === 'echarts') {
    // describe_series 特殊处理
    if (lang && lang.toLowerCase().startsWith('describe')) {
      const option = buildDescribeSeriesOption(trimmed);
      if (option) return option;
    }
    const option = parseEchartsOption(trimmed);
    if (option) return option;
    return { text: trimmed };
  }

  if (blockType === 'structured') {
    try {
      return JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      return { text: trimmed };
    }
  }

  // table / text → 原文
  return { text: trimmed };
}

/**
 * 构建 Markdown 表格区块的 content_snapshot。
 *
 * @param columns 列名数组
 * @param rows 行数据二维数组
 */
export function buildTableSnapshot(
  columns: string[],
  rows: unknown[][],
): Record<string, unknown> {
  return { columns, rows };
}

export default buildContentSnapshot;
