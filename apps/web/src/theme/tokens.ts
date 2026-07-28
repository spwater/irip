/**
 * Data Ocean — Polar Mist 语义 Token
 *
 * 不可变 token 对象，所有颜色/圆角/动效值均为设计规范指定的精确值。
 * 组件层通过 statusTone 映射获取状态语义，不直接引用原始色值。
 */

/** 状态语调类型 — 用于 StatusMark 和 FlowTrack 等组件 */
export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'special';

/** 语义 token 对象（不可变） */
export const oceanTokens = {
  /** 画布三色 — Polar Mist 基础渐变 */
  canvas: { start: '#A9D2DF', middle: '#CFE5EA', end: '#E8F3F5' },
  /** 表面层级 — 透明度递进，营造空间深度 */
  surface: {
    default: 'rgba(240, 250, 251, 0.72)',
    strong: 'rgba(232, 246, 249, 0.90)',
    structural: 'rgba(142, 191, 208, 0.46)',
    focus: '#6FA9BE',
  },
  /** 文本层级 */
  text: { primary: '#102F44', secondary: '#486B7E', muted: '#6F8D9C' },
  /** 边框层级 */
  border: {
    subtle: 'rgba(24, 102, 133, 0.16)',
    strong: 'rgba(14, 118, 156, 0.34)',
  },
  /** 操作色 */
  action: { primary: '#1686AE', hover: '#0E769C', active: '#075C7D' },
  /** 强调色 */
  accent: { current: '#39B9C2' },
  /** 状态色 — 六种语义状态 */
  status: {
    success: '#14765E',
    warning: '#9A6818',
    danger: '#A53D52',
    info: '#245F9A',
    violet: '#6655A4',
    neutral: '#6F8D9C',
  },
  /** 圆角 */
  radius: { control: 4, panel: 6, overlay: 8 },
  /** 动效时长（毫秒） */
  motion: { instant: 120, control: 180, enter: 240, focus: 320, page: 360 },
} as const;

/** 状态 → 颜色 + 标记形状映射，供 StatusMark / FlowTrack 使用 */
export const statusTone = {
  neutral: { color: oceanTokens.status.neutral, marker: 'outline' },
  info: { color: oceanTokens.status.info, marker: 'solid' },
  success: { color: oceanTokens.status.success, marker: 'solid' },
  warning: { color: oceanTokens.status.warning, marker: 'diamond' },
  danger: { color: oceanTokens.status.danger, marker: 'cross' },
  special: { color: oceanTokens.status.violet, marker: 'ring' },
} as const;
