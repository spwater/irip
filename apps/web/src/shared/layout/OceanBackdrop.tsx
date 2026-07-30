/**
 * OceanBackdrop — 全局背景
 *
 * 固定定位的背景层，承载极地雾蓝渐变 + 网格 + 光域 + 装饰。
 * - z-index: 0
 * - 不承载业务数据
 * - 不持续跟随鼠标或制造景深晃动
 *
 * 视觉规则来自设计文档第 7.3 节与 styles/ocean.css。
 */
import type { CSSProperties, ReactNode } from 'react';

interface OceanBackdropProps {
  /** 可选装饰内容（如背景大字索引），不承载业务数据 */
  children?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
}

/**
 * 全局极地雾蓝背景。
 *
 * 渲染结构：
 *   <div class="ocean-backdrop">
 *     <div class="ocean-backdrop__grid" />
 *     <div class="ocean-backdrop__glow ocean-backdrop__glow--primary" />
 *     <div class="ocean-backdrop__glow ocean-backdrop__glow--secondary" />
 *     <div class="ocean-backdrop__decoration">{children}</div>
 *   </div>
 */
export function OceanBackdrop({ children, style }: OceanBackdropProps): JSX.Element {
  return (
    <div className="ocean-backdrop" style={style} aria-hidden="true">
      {/* 低透明度网格层 */}
      <div className="ocean-backdrop__grid" />
      {/* 大范围光域 */}
      <div className="ocean-backdrop__glow ocean-backdrop__glow--primary" />
      <div className="ocean-backdrop__glow ocean-backdrop__glow--secondary" />
      {/* 装饰层（背景大字等，透明度 ≤ 4%） */}
      {children ? (
        <div className="ocean-backdrop__decoration">{children}</div>
      ) : null}
    </div>
  );
}

export default OceanBackdrop;
