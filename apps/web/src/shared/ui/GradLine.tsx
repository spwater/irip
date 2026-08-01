/**
 * GradLine — 渐变直线「潮线 Tideline」
 *
 * 一道左深右浅的圆头渐变细线（深潮蓝 → 潮流青 → 透明）。
 * 用于品牌区、Hero 数字下方等需要克制的流动标记的位置。
 * 纯装饰，aria-hidden。
 */
import type { CSSProperties } from 'react';

export interface GradLineProps {
  /** 宽度（px 或 CSS 长度），默认 120 */
  width?: number | string;
  /** 线宽，默认 2 */
  thickness?: number;
  /** 透明度，默认 0.85 */
  opacity?: number;
  /** 透传样式 */
  style?: CSSProperties;
}

export function GradLine({
  width = 120,
  thickness = 2,
  opacity = 0.85,
  style,
}: GradLineProps): JSX.Element {
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'block',
        width,
        height: thickness,
        borderRadius: 999,
        background:
          'linear-gradient(90deg, rgba(14, 91, 132, 0.9) 0%, rgba(23, 184, 206, 0.6) 55%, rgba(23, 184, 206, 0) 100%)',
        opacity,
        ...style,
      }}
    />
  );
}

export default GradLine;
