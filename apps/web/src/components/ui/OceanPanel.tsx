/**
 * OceanPanel — 稳定表面面板
 *
 * 提供三级透明度表面：default / strong / structural。
 * 可按语义选择渲染容器标签：section / article / div。
 */
import { createElement, type PropsWithChildren } from 'react';

export type OceanPanelProps = PropsWithChildren<{
  /** 容器语义标签 */
  as?: 'section' | 'article' | 'div';
  /** 表面层级 */
  level?: 'default' | 'strong' | 'structural';
  /** 额外 class */
  className?: string;
}>;

export function OceanPanel({
  as = 'section',
  level = 'default',
  className,
  children,
}: OceanPanelProps): JSX.Element {
  const classes = ['ocean-panel', `ocean-panel--${level}`];
  if (className) classes.push(className);
  return createElement(as, { className: classes.join(' ') }, children);
}
