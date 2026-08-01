/**
 * EcgLine — 心电图脉搏图示「潮线 Tideline」
 *
 * 系统健康状态的可视化：底层淡色完整波形 + 上层亮纹沿波形跑动。
 * 颜色随状态语义变化（success/warning/danger）。
 * stretch 模式下波形横向拉伸填满宽度，作为面板主视觉。
 * 纯装饰，aria-hidden；reduced-motion 下亮纹静止。
 */
import type { CSSProperties } from 'react';
import type { StatusSemantic } from '@/theme/tokens';

export interface EcgLineProps {
  /** 状态语义：success 青绿 / warning 琥珀 / danger 珊瑚，默认 success */
  status?: StatusSemantic;
  /** 宽度（px 或 '100%'），默认 120 */
  width?: number | string;
  /** 高度，默认 28 */
  height?: number;
  /** 拉伸模式：波形横向撑满宽度（大图主视觉） */
  stretch?: boolean;
  /** 透传样式 */
  style?: CSSProperties;
}

/** 状态 → 波形颜色对（亮纹 / 底层） */
const STATUS_COLORS: Record<string, { bright: string; dim: string }> = {
  success: { bright: '#10B981', dim: 'rgba(16, 185, 129, 0.22)' },
  info: { bright: '#17B8CE', dim: 'rgba(23, 184, 206, 0.22)' },
  warning: { bright: '#B87A1E', dim: 'rgba(154, 104, 24, 0.22)' },
  danger: { bright: '#A53D52', dim: 'rgba(165, 61, 82, 0.22)' },
  neutral: { bright: '#6F8D9C', dim: 'rgba(111, 141, 156, 0.22)' },
  special: { bright: '#6655A4', dim: 'rgba(102, 85, 164, 0.22)' },
};

/** 心电波形：基线 — 小波 — 主尖峰 — 回落波 — 基线 */
const ECG_PATH =
  'M0 16 L18 16 L24 12 L30 20 L36 16 L44 16 ' +
  'L50 4 L56 26 L62 16 L74 16 L80 12 L86 20 L92 16 L104 16 L110 10 L116 16 L140 16';

export function EcgLine({
  status = 'success',
  width = 120,
  height = 28,
  stretch = false,
  style,
}: EcgLineProps): JSX.Element {
  const colors = STATUS_COLORS[status] ?? STATUS_COLORS.success;

  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 140 32"
      fill="none"
      aria-hidden="true"
      className="ocean-ecg"
      preserveAspectRatio={stretch ? 'none' : 'xMidYMid meet'}
      style={{ display: 'block', ...style }}
    >
      {/* 底层：完整波形（淡） */}
      <path
        d={ECG_PATH}
        stroke={colors.dim}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {/* 上层：亮纹沿波形跑动 */}
      <path
        d={ECG_PATH}
        stroke={colors.bright}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray="36 264"
        className="ocean-ecg__pulse"
        vectorEffect="non-scaling-stroke"
      />
      <style>{`
        .ocean-ecg__pulse {
          animation: ocean-ecg-run 3.2s linear infinite;
        }
        @keyframes ocean-ecg-run {
          from { stroke-dashoffset: 300; }
          to { stroke-dashoffset: 0; }
        }
        @media (prefers-reduced-motion: reduce) {
          .ocean-ecg__pulse {
            animation: none;
            stroke-dasharray: none;
          }
        }
      `}</style>
    </svg>
  );
}

export default EcgLine;
