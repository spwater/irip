/**
 * DataHero — 数据英雄区
 *
 * 展示单个关键指标的标签、数值（tabular-nums）和单位。
 * 数值使用 ocean-tabular-number 确保数字等宽对齐。
 */
import type { ReactNode } from 'react';

export type DataHeroProps = {
  /** 指标标签 */
  label: string;
  /** 指标数值 */
  value: ReactNode;
  /** 数值单位 */
  unit?: string;
  /** 补充说明 */
  summary?: ReactNode;
};

export function DataHero({ label, value, unit, summary }: DataHeroProps): JSX.Element {
  return (
    <div className="ocean-data-hero">
      <span className="ocean-data-hero__label">{label}</span>
      <div className="ocean-data-hero__value-row">
        <span className="ocean-data-hero__value ocean-tabular-number">{value}</span>
        {unit ? <span className="ocean-data-hero__unit">{unit}</span> : null}
      </div>
      {summary ? <div className="ocean-data-hero__summary">{summary}</div> : null}
    </div>
  );
}
