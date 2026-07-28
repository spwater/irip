/**
 * MetricStrip — 指标条
 *
 * 水平排列多个指标项，每项包含标签、数值、单位和来源注记。
 * 按传入顺序渲染 DOM，CSS 负责视觉布局偏移。
 */
import type { ReactNode } from 'react';

export type MetricItem = {
  /** 唯一键 */
  key: string;
  /** 指标标签 */
  label: string;
  /** 指标数值 */
  value: ReactNode;
  /** 数值单位 */
  unit?: string;
  /** 来源注记（对用户可见） */
  note?: ReactNode;
  /** 视觉语调 */
  tone?: 'default' | 'focus';
};

export type MetricStripProps = {
  items: MetricItem[];
};

export function MetricStrip({ items }: MetricStripProps): JSX.Element {
  return (
    <div className="ocean-metric-strip" role="list">
      {items.map((item) => (
        <div
          key={item.key}
          className="ocean-metric-strip__item"
          role="listitem"
          data-tone={item.tone ?? 'default'}
        >
          <span className="ocean-metric-strip__label">{item.label}</span>
          <div className="ocean-metric-strip__value-row">
            <span className="ocean-metric-strip__value ocean-tabular-number">
              {item.value}
            </span>
            {item.unit ? <span className="ocean-metric-strip__unit">{item.unit}</span> : null}
          </div>
          {item.note ? <div className="ocean-metric-strip__note">{item.note}</div> : null}
        </div>
      ))}
    </div>
  );
}
