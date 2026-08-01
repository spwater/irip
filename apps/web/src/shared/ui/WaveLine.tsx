/**
 * WaveLine — 波形细线「潮线 Tideline」
 *
 * 一道平缓的 SVG 波浪，深潮蓝 → 潮流青渐变描边。
 * 用于品牌区、Hero 下方、登录页等需要"流动感"标记的位置。
 * 纯装饰，aria-hidden。
 */
import type { CSSProperties } from 'react';

export interface WaveLineProps {
  /** 宽度（px 或 CSS 长度），默认 120 */
  width?: number | string;
  /** 高度，默认 8 */
  height?: number;
  /** 透明度，默认 0.8 */
  opacity?: number;
  /** 透传样式 */
  style?: CSSProperties;
}

/**
 * 波形细线。viewBox 240×16，path 为两段平缓正弦波。
 * 渐变从深潮蓝 (#0E5B84) 流向潮流青 (#17B8CE) 再淡出。
 */
export function WaveLine({
  width = 120,
  height = 8,
  opacity = 0.8,
  style,
}: WaveLineProps): JSX.Element {
  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 240 16"
      fill="none"
      aria-hidden="true"
      style={{ display: 'block', ...style }}
    >
      <defs>
        <linearGradient id="ocean-waveline-gradient" x1="0" y1="0" x2="240" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#0E5B84" stopOpacity="0.9" />
          <stop offset="0.55" stopColor="#17B8CE" stopOpacity="0.85" />
          <stop offset="1" stopColor="#17B8CE" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path
        d="M0 8 C 30 2, 60 14, 90 8 C 120 2, 150 14, 180 8 C 200 4, 220 6, 240 8"
        stroke="url(#ocean-waveline-gradient)"
        strokeWidth="2"
        strokeLinecap="round"
        opacity={opacity}
      />
    </svg>
  );
}

export default WaveLine;
