/**
 * DataHero — 核心数字视觉「潮线 Tideline」
 *
 * 职责：
 * - 一个核心数字、名称或状态
 *
 * 禁止：不自行计算统计值。
 *
 * 视觉规则：
 * - 核心数字 48–96px，tabular-nums
 * - deep 变体：深潮渐变块 + 右下角斜切 + 亮青数字（看板/总览主视觉）
 * - 默认变体：浅底亮青数字
 */
import type { CSSProperties } from 'react';
import { Typography } from 'antd';
import { STATUS_SEMANTIC_COLOR, type StatusSemantic } from '@/theme/tokens';

const { Text } = Typography;

export interface DataHeroProps {
  /** 核心数字或主标识 */
  value: string | number;
  /** 标签说明（统计口径） */
  label: string;
  /** 单位（如 次、条、个） */
  unit?: string;
  /** 状态语义，用于强调色 */
  status?: StatusSemantic;
  /** 深潮变体：深蓝渐变块上的亮青数字（第一眼主视觉） */
  deep?: boolean;
  /** 透传样式 */
  style?: CSSProperties;
}

/**
 * 核心数据主视觉。
 *
 * 结构：
 *   标签（小字、次级）
 *   核心数字（大字、tabular-nums）
 *   单位（紧贴数字右侧）
 */
export function DataHero({
  value,
  label,
  unit,
  status,
  deep = false,
  style,
}: DataHeroProps): JSX.Element {
  const accentColor = status
    ? STATUS_SEMANTIC_COLOR[status]
    : deep
      ? 'var(--ocean-current-on-deep)'
      : 'var(--ocean-current-bright)';

  const labelColor = deep ? 'rgba(234, 246, 249, 0.72)' : 'var(--ocean-text-secondary)';
  const unitColor = deep ? 'rgba(234, 246, 249, 0.72)' : 'var(--ocean-text-secondary)';

  const body = (
    <>
      <Text
        className="ocean-data-hero__label"
        style={{
          display: 'block',
          fontSize: 13,
          color: labelColor,
          marginBottom: 4,
        }}
      >
        {label}
      </Text>
      <div
        className="ocean-data-hero__value-row"
        style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}
      >
        <span
          className="ocean-data-hero__value ocean-tabular-nums"
          style={{
            fontSize: 72,
            fontWeight: deep ? 700 : 400,
            lineHeight: 1.05,
            color: accentColor,
            fontVariantNumeric: 'tabular-nums',
            fontFeatureSettings: '"tnum"',
            letterSpacing: '-0.5px',
          }}
        >
          {value}
        </span>
        {unit ? (
          <Text
            className="ocean-data-hero__unit"
            style={{
              fontSize: 16,
              color: unitColor,
              fontWeight: 500,
            }}
          >
            {unit}
          </Text>
        ) : null}
      </div>
    </>
  );

  if (deep) {
    return (
      <div
        className="ocean-data-hero ocean-abyss-block ocean-tide-enter"
        style={{
          display: 'inline-block',
          padding: '24px 48px 28px 28px',
          clipPath: 'polygon(0 0, 100% 0, 100% calc(100% - 22px), calc(100% - 22px) 100%, 0 100%)',
          boxShadow: '0 20px 48px rgba(7, 51, 78, 0.28)',
          ...style,
        }}
      >
        {body}
      </div>
    );
  }

  return (
    <div className="ocean-data-hero" style={style}>
      {body}
    </div>
  );
}

export default DataHero;
