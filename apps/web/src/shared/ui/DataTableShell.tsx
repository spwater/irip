/**
 * DataTableShell — 表格外壳
 *
 * 职责（设计文档第 8.1 节）：
 * - 表格标题、Table、分页和反馈状态
 *
 * 禁止：不封装 columns 业务定义。
 *
 * 视觉规则（第 8.3 节）：
 * - 表格所在面板使用稳定的 surface.strong，不让背景纹理穿透数据行
 * - 空表格在表体内部显示解释和主操作
 * - 横向滚动只出现在表格容器内，不允许页面级横向溢出
 */
import type { CSSProperties, ReactNode } from 'react';
import { Typography } from 'antd';

const { Title } = Typography;

export interface DataTableShellProps {
  /** 表格标题 */
  title?: string;
  /** 表格内容（Ant Design Table 等） */
  children?: ReactNode;
  /** 底部区域（如分页、汇总） */
  footer?: ReactNode;
  /** 标题右侧操作区 */
  extra?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
  /** 自定义 className */
  className?: string;
  /** 内边距，默认 0（表格通常贴边） */
  bodyPadding?: number | string;
}

/**
 * 表格外壳容器。
 *
 * 使用 strong surface 承载，横向滚动限制在容器内部。
 */
export function DataTableShell({
  title,
  children,
  footer,
  extra,
  style,
  className,
  bodyPadding = 0,
}: DataTableShellProps): JSX.Element {
  return (
    <section
      className={`ocean-data-table-shell${className ? ` ${className}` : ''}`}
      style={{
        background: 'var(--ocean-surface-strong)',
        border: '1px solid var(--ocean-border-subtle)',
        borderRadius: 6,
        overflow: 'hidden',
        ...style,
      }}
    >
      {(title || extra) && (
        <header
          className="ocean-data-table-shell__header"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            padding: '12px 16px',
            borderBottom: '1px solid var(--ocean-border-subtle)',
          }}
        >
          {title ? (
            <Title
              level={5}
              style={{
                margin: 0,
                fontSize: 15,
                fontWeight: 600,
                color: 'var(--ocean-text-primary)',
              }}
            >
              {title}
            </Title>
          ) : (
            <span />
          )}
          {extra ? (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{extra}</div>
          ) : null}
        </header>
      )}
      {/* 表格内容区：横向滚动限制在容器内 */}
      <div
        className="ocean-data-table-shell__body"
        style={{
          padding: bodyPadding,
          overflowX: 'auto',
        }}
      >
        {children}
      </div>
      {footer ? (
        <footer
          className="ocean-data-table-shell__footer"
          style={{
            padding: '10px 16px',
            borderTop: '1px solid var(--ocean-border-subtle)',
            background: 'rgba(232, 246, 249, 0.5)',
          }}
        >
          {footer}
        </footer>
      ) : null}
    </section>
  );
}

export default DataTableShell;
