/**
 * ActionBar — 筛选与操作布局
 *
 * 职责（设计文档第 8.1 节）：
 * - 搜索、筛选、批量操作、主操作
 *
 * 禁止：不改变筛选值语义。
 *
 * 视觉规则：
 * - 使用 strong surface 承载，避免背景纹理穿透
 * - 左侧筛选区，右侧主操作区
 */
import type { CSSProperties, ReactNode } from 'react';

export interface ActionBarProps {
  /** 筛选与操作内容 */
  children?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
  /** 自定义 className */
  className?: string;
}

/**
 * 筛选与操作布局条。
 *
 * 结构：左侧筛选控件，右侧主操作；flex 布局，窄屏自动换行。
 */
export function ActionBar({
  children,
  style,
  className,
}: ActionBarProps): JSX.Element {
  return (
    <div
      className={`ocean-action-bar${className ? ` ${className}` : ''}`}
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        padding: '12px 16px',
        background: 'var(--ocean-surface-strong)',
        border: '1px solid var(--ocean-border-subtle)',
        borderRadius: 6,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export default ActionBar;
