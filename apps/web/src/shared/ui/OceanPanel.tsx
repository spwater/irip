/**
 * OceanPanel — 三层面板
 *
 * 职责（设计文档第 8.1 节）：
 * - default / strong / structural 三层面板
 *
 * 禁止：不隐藏业务滚动。
 *
 * 视觉规则：
 * - default：常规内容面板（半透明）
 * - strong：表单、表格、详情主面板（稳定 surface，不使用实时 backdrop-filter）
 * - structural：导航、分区和结构层
 */
import type { CSSProperties, ReactNode } from 'react';

export type OceanPanelVariant = 'default' | 'strong' | 'structural';

export interface OceanPanelProps {
  /** 面板层级 */
  variant?: OceanPanelVariant;
  /** 内容 */
  children?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
  /** 自定义 className */
  className?: string;
  /** 内边距 */
  padding?: number | string;
}

/** 变体 → 背景材质映射 */
const VARIANT_BG: Record<OceanPanelVariant, string> = {
  default: 'var(--ocean-surface-default)',
  strong: 'var(--ocean-surface-strong)',
  structural: 'var(--ocean-surface-structural)',
};

/**
 * 透明面板材质容器。
 *
 * 使用 CSS 变量引用 tokens，strong 变体不使用 backdrop-filter 以保证滚动性能。
 */
export function OceanPanel({
  variant = 'default',
  children,
  style,
  className,
  padding = 16,
}: OceanPanelProps): JSX.Element {
  return (
    <div
      className={`ocean-panel ocean-panel--${variant}${className ? ` ${className}` : ''}`}
      style={{
        background: VARIANT_BG[variant],
        border: '1px solid var(--ocean-border-subtle)',
        borderRadius: variant === 'structural' ? 4 : 6,
        padding,
        position: 'relative',
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export default OceanPanel;
