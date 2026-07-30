/**
 * DetailSection — 详情分区
 *
 * 职责（设计文档第 8.1 节）：
 * - 详情信息分组
 *
 * 禁止：不改变字段值与单位。
 *
 * 视觉规则：
 * - 使用 strong surface 承载
 * - 标题 + 右侧 extra 操作
 */
import type { CSSProperties, ReactNode } from 'react';
import { Typography } from 'antd';

const { Title } = Typography;

export interface DetailSectionProps {
  /** 分区标题 */
  title: string;
  /** 标题右侧附加操作或状态 */
  extra?: ReactNode;
  /** 分区内容 */
  children?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
  /** 自定义 className */
  className?: string;
  /** 是否默认展开（预留，当前始终展开） */
  defaultExpanded?: boolean;
}

/**
 * 详情信息分组容器。
 *
 * 适用于详情页的“元数据 / 过程与版本 / 结果工件 / 技术信息”分区。
 */
export function DetailSection({
  title,
  extra,
  children,
  style,
  className,
}: DetailSectionProps): JSX.Element {
  return (
    <section
      className={`ocean-detail-section${className ? ` ${className}` : ''}`}
      style={{
        background: 'var(--ocean-surface-strong)',
        border: '1px solid var(--ocean-border-subtle)',
        borderRadius: 6,
        overflow: 'hidden',
        ...style,
      }}
    >
      <header
        className="ocean-detail-section__header"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          padding: '12px 16px',
          borderBottom: '1px solid var(--ocean-border-subtle)',
        }}
      >
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
        {extra ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{extra}</div>
        ) : null}
      </header>
      <div className="ocean-detail-section__body" style={{ padding: 16 }}>
        {children}
      </div>
    </section>
  );
}

export default DetailSection;
