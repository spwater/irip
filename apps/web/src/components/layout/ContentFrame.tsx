/**
 * ContentFrame — 内容框架
 *
 * - 最大宽度 1680px 居中
 * - 响应式边距：1280px→20px，1440px→28px，1920px→32px
 * - z-index: 10
 *
 * 设计文档第 6.4 节与第 7.2 节定义的内容布局边界。
 */
import type { CSSProperties, ReactNode } from 'react';

interface ContentFrameProps {
  /** 内容 */
  children?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
  /** 自定义 className */
  className?: string;
}

/**
 * 内容框架容器。
 *
 * 响应式边距通过 CSS clamp 实现：
 * - 1280px 视口 → 20px 边距
 * - 1440px 视口 → 28px 边距
 * - 1920px 视口 → 32px 边距
 *
 * 内容最大宽度 1680px，超宽屏居中，背景延展到全屏。
 */
export function ContentFrame({
  children,
  style,
  className,
}: ContentFrameProps): JSX.Element {
  return (
    <div
      className={`ocean-content-frame${className ? ` ${className}` : ''}`}
      style={{
        position: 'relative',
        zIndex: 10,
        width: '100%',
        maxWidth: '1680px',
        margin: '0 auto',
        // 响应式边距：1280→20px, 1440→28px, 1920→32px
        padding: '0 clamp(20px, 1.4vw, 32px) 24px',
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export default ContentFrame;
