/**
 * ActionBar — 操作栏
 *
 * 纯展示壳：左侧筛选区、中间摘要、右侧操作区。
 * 不持有业务状态，由业务页面传入内容。
 */
import type { ReactNode } from 'react';

export type ActionBarProps = {
  /** 筛选器插槽 */
  filters?: ReactNode;
  /** 操作按钮插槽 */
  actions?: ReactNode;
  /** 摘要信息插槽 */
  summary?: ReactNode;
};

export function ActionBar({ filters, actions, summary }: ActionBarProps): JSX.Element {
  return (
    <div className="ocean-action-bar">
      {filters ? <div className="ocean-action-bar__filters">{filters}</div> : null}
      {summary ? <div className="ocean-action-bar__summary">{summary}</div> : null}
      {actions ? <div className="ocean-action-bar__actions">{actions}</div> : null}
    </div>
  );
}
