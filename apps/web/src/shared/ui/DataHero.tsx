/**
 * DataHero — 核心数字视觉
 *
 * 职责（设计文档第 8.1 节）：
 * - 一个核心数字、名称或状态
 *
 * 禁止：不自行计算统计值。
 *
 * 视觉规则（第 4.1 节、第 6.3 节）：
 * - 核心数字允许达到 48–96px，字重 300–500
 * - 使用 font-variant-numeric: tabular-nums
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
  style,
}: DataHeroProps): JSX.Element {
  const accentColor = status ? STATUS_SEMANTIC_COLOR[status] : 'var(--ocean-accent-current)';

  return (
    <div className="ocean-data-hero" style={style}>
      <Text
        className="ocean-data-hero__label"
        style={{
          display: 'block',
          fontSize: 13,
          color: 'var(--ocean-text-secondary)',
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
            fontWeight: 400,
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
              color: 'var(--ocean-text-secondary)',
              fontWeight: 500,
            }}
          >
            {unit}
          </Text>
        ) : null}
      </div>
    </div>
  );
}

export default DataHero;
