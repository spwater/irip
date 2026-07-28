/**
 * Data Ocean — ECharts 主题辅助
 *
 * createOceanChartOptions 在不覆盖业务 series / data / unit / grid 的前提下，
 * 合并 Polar Mist 视觉默认值（透明背景、语义文字色、调色板、动画降级）。
 *
 * 设计约束：
 * - source 中的 series、grid、yAxis 等字段优先级高于默认值
 * - reducedMotion=true 时关闭动画
 * - 不修改任何数据相关字段
 */
import type { EChartsOption } from 'echarts';

/**
 * 创建 Data Ocean 风格的 ECharts 选项。
 *
 * @param source — 业务传入的原始 ECharts 选项（包含 series / data / grid / yAxis 等）
 * @param reducedMotion — 是否为 reduced-motion 模式（关闭动画）
 * @returns 合并后的 ECharts 选项，不覆盖 source 中的 series / grid / yAxis
 */
export function createOceanChartOptions(
  source: EChartsOption,
  reducedMotion: boolean,
): EChartsOption {
  const sourceGrid = source.grid;
  const sourceSeries = source.series;

  return {
    backgroundColor: 'transparent',
    textStyle: { color: '#486B7E' },
    animation: !reducedMotion,
    color: ['#1686AE', '#39B9C2', '#6FA9BE', '#6655A4', '#14765E', '#9A6818'],
    ...source,
    // 确保业务传入的 grid / series 不被默认值覆盖
    grid: sourceGrid,
    series: sourceSeries,
  };
}
