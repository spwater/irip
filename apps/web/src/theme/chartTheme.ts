/**
 * Data Ocean ECharts 统一主题
 *
 * 按设计文档第 11.1 节：
 * - 图表背景默认透明，由稳定 OceanPanel 承载
 * - 主色序列：#1686AE、#39B9C2、#6FA9BE、#6655A4、#14765E、#9A6818
 * - 坐标轴文字 #486B7E，分隔线 rgba(24, 102, 133, 0.12)
 * - tooltip 使用 surface.strong、清晰边框、深色文字，必须显示单位
 *
 * 使用方式：在 ECharts 实例合并 chartTheme，但不得覆盖业务生成的
 * series、数据或单位。
 */
import { tokens } from './tokens';

/** 主色序列（建议 6 色，按文档顺序） */
export const CHART_COLOR_SEQUENCE: string[] = [
  '#1686AE',
  '#39B9C2',
  '#6FA9BE',
  '#6655A4',
  '#14765E',
  '#9A6818',
];

/**
 * Data Ocean ECharts 主题对象
 *
 * 结构遵循 ECharts option 形态，调用方通过 `echartsInstance.setOption(chartTheme)`
 * 或在业务 option 中合并使用。颜色、坐标轴、tooltip 为视觉层，不得覆盖
 * 业务计算后的 series/grid 数据。
 */
export const chartTheme = {
  // 图表背景透明，由 OceanPanel 承载稳定 surface
  backgroundColor: 'transparent',
  // 调色板
  color: CHART_COLOR_SEQUENCE,
  // 全局文字
  textStyle: {
    color: tokens.ocean.text.secondary,
    fontFamily: tokens.typography.fontFamily,
    fontSize: 12,
  },
  // 标题
  title: {
    color: tokens.ocean.text.primary,
    fontSize: 16,
    fontWeight: 600,
  },
  // 坐标轴
  categoryAxis: {
    axisLine: {
      show: true,
      lineStyle: {
        color: 'rgba(24, 102, 133, 0.20)',
        width: 1,
      },
    },
    axisTick: {
      show: false,
    },
    axisLabel: {
      color: tokens.ocean.text.secondary,
      fontSize: 12,
    },
    splitLine: {
      show: false,
    },
    splitArea: {
      show: false,
    },
  },
  valueAxis: {
    axisLine: {
      show: false,
    },
    axisTick: {
      show: false,
    },
    axisLabel: {
      color: tokens.ocean.text.secondary,
      fontSize: 12,
    },
    splitLine: {
      show: true,
      lineStyle: {
        color: 'rgba(24, 102, 133, 0.12)',
        width: 1,
      },
    },
    splitArea: {
      show: false,
    },
  },
  // 提示框：surface.strong + 清晰边框 + 深色文字
  tooltip: {
    backgroundColor: tokens.ocean.surface.strong,
    borderColor: tokens.ocean.border.strong,
    borderWidth: 1,
    textStyle: {
      color: tokens.ocean.text.primary,
      fontSize: 12,
    },
    extraCssText: 'box-shadow: 0 24px 64px rgba(29, 78, 103, 0.18); border-radius: 6px;',
  },
  // 图例
  legend: {
    textStyle: {
      color: tokens.ocean.text.secondary,
      fontSize: 12,
    },
    inactiveColor: tokens.ocean.text.muted,
    icon: 'roundRect',
    itemWidth: 12,
    itemHeight: 8,
  },
  // 数据区域缩放
  dataZoom: {
    backgroundColor: 'rgba(142, 191, 208, 0.12)',
    fillerColor: 'rgba(22, 134, 174, 0.14)',
    handleColor: tokens.ocean.action.primary,
    textStyle: {
      color: tokens.ocean.text.secondary,
    },
  },
  // 折线/柱状默认样式
  line: {
    smooth: false,
    symbol: 'circle',
    symbolSize: 6,
    lineStyle: {
      width: 2,
    },
  },
  bar: {
    itemStyle: {
      borderRadius: [2, 2, 0, 0],
    },
  },
  // 饼图
  pie: {
    itemStyle: {
      borderColor: tokens.ocean.surface.strong,
      borderWidth: 2,
    },
    label: {
      color: tokens.ocean.text.secondary,
    },
  },
  // 仪表盘
  gauge: {
    axisLine: {
      lineStyle: {
        color: [
          [0.6, tokens.ocean.action.primary],
          [1, tokens.ocean.status.warning],
        ],
      },
    },
  },
  // 关系图节点与边
  graph: {
    color: CHART_COLOR_SEQUENCE,
    label: {
      color: tokens.ocean.text.primary,
      fontSize: 12,
    },
    lineStyle: {
      color: 'rgba(24, 102, 133, 0.28)',
      width: 1,
      curveness: 0.1,
      opacity: 0.8,
    },
    itemStyle: {
      borderColor: tokens.ocean.border.strong,
      borderWidth: 1,
    },
  },
  // 动画：仅首次轨迹渐显；高频更新与 reduced motion 下由调用方关闭
  animation: true,
  animationDuration: 480,
  animationDurationUpdate: 240,
  animationEasing: 'cubicOut',
  animationEasingUpdate: 'cubicOut',
};

/**
 * reduced motion 下使用的图表配置片段。
 *
 * 调用方在 `prefers-reduced-motion: reduce` 时合并此片段，关闭初始与更新动画，
 * 同时 Progress 仍表达真实进度，但不显示无意义循环动画。
 */
export const chartThemeReducedMotion = {
  animation: false,
  animationDuration: 0,
  animationDurationUpdate: 0,
  animationEasing: 'linear',
  animationEasingUpdate: 'linear',
  progress: {
    animation: false,
  },
} as const;

export default chartTheme;
