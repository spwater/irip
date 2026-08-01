/**
 * MetricStrip — 摘要指标布局
 *
 * 职责（设计文档第 8.1 节）：
 * - 2–4 个摘要指标
 *
 * 禁止：不强制同宽卡片墙。
 *
 * 视觉规则：
 * - 使用 OceanPanel default 承载
 * - 指标数字使用 tabular-nums
 * - 指标名称必须说明统计口径（由调用方在 label 中提供）
 */
import type { CSSProperties } from 'react';
import { Typography } from 'antd';

const { Text } = Typography;

export interface MetricItem {
  /** 指标名称（需说明统计口径，如“最近 7 天事实数”） */
  label: string;
  /** 指标值 */
  value: string | number;
  /** 单位 */
  unit?: string;
  /** 次级说明 */
  hint?: string;
}

export interface MetricStripProps {
  /** 2–4 个摘要指标 */
  metrics: MetricItem[];
  /** 透传样式 */
  style?: CSSProperties;
  /** 自定义 className */
  className?: string;
}

/**
 * 摘要指标条。
 *
 * 不强制同宽，使用 CSS 自适应布局；指标数建议 2–4 个。
 */
export function MetricStrip({
  metrics,
  style,
  className,
}: MetricStripProps): JSX.Element {
  const count = Math.max(1, Math.min(metrics.length, 4));
  return (
    <div
      className={`ocean-metric-strip${className ? ` ${className}` : ''}`}
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))`,
        gap: 12,
        ...style,
      }}
    >
      {metrics.map((m) => (
        <div
          key={m.label}
          className="ocean-metric-strip__item"
          style={{
            position: 'relative',
            background: 'var(--ocean-surface-default)',
            border: '1px solid var(--ocean-border-subtle)',
            borderRadius: 10,
            padding: '14px 16px',
            overflow: 'hidden',
          }}
        >
          {/* 顶部水光渐变线 */}
          <span
            aria-hidden="true"
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 2,
              background: 'linear-gradient(90deg, rgba(14, 91, 132, 0.45) 0%, rgba(23, 184, 206, 0.4) 46%, rgba(23, 184, 206, 0) 82%)',
            }}
          />
          <Text
            style={{
              display: 'block',
              fontSize: 12,
              color: 'var(--ocean-text-secondary)',
              marginBottom: 6,
              lineHeight: 1.4,
            }}
          >
            {m.label}
          </Text>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
            <span
              className="ocean-tabular-nums"
              style={{
                fontSize: 30,
                fontWeight: 700,
                lineHeight: 1.1,
                color: '#0E5B84',
                fontVariantNumeric: 'tabular-nums',
                fontFeatureSettings: '"tnum"',
                letterSpacing: '-0.5px',
              }}
            >
              {m.value}
            </span>
            {m.unit ? (
              <Text style={{ fontSize: 13, color: 'var(--ocean-text-secondary)' }}>
                {m.unit}
              </Text>
            ) : null}
          </div>
          {m.hint ? (
            <Text
              style={{
                display: 'block',
                marginTop: 4,
                fontSize: 12,
                color: 'var(--ocean-text-muted)',
              }}
            >
              {m.hint}
            </Text>
          ) : null}
        </div>
      ))}
    </div>
  );
}

export default MetricStrip;
