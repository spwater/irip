/**
 * PageIntro — 页面标题区
 *
 * 职责（设计文档第 8.1 节）：
 * - 页面中文标题
 * - 可选英文索引或模块编号
 * - 一句用途说明
 * - 0–2 个主要操作
 *
 * 禁止：不请求数据，不内置路由。
 */
import type { CSSProperties, ReactNode } from 'react';
import { Typography } from 'antd';
import type { SpaceProps } from 'antd';

const { Title, Text, Paragraph } = Typography;

export interface PageIntroProps {
  /** 页面中文标题 */
  title: string;
  /** 可选英文索引或模块编号（如 "MODULE 03 / LAB OPERATIONS"） */
  index?: string;
  /** 一句用途说明 */
  subtitle?: string;
  /** 0–2 个主要操作（按钮等） */
  actions?: ReactNode;
  /** 标题区底部附加内容（如 DataHero、MetricStrip） */
  children?: ReactNode;
  /** 操作区对齐方式 */
  actionsAlign?: SpaceProps['align'];
  /** 透传样式 */
  style?: CSSProperties;
}

/**
 * 页面标题原型。
 *
 * 结构：
 *   英文索引（小字距）
 *   中文标题（大字）
 *   用途说明
 *   操作区（右上对齐）
 *   附加内容
 */
export function PageIntro({
  title,
  index,
  subtitle,
  actions,
  children,
  style,
}: PageIntroProps): JSX.Element {
  return (
    <section className="ocean-page-intro" style={{ marginBottom: 24, ...style }}>
      {/* 英文索引：10–12px、大字距，只作为结构索引 */}
      {index ? (
        <Text
          className="ocean-page-intro__index"
          style={{
            display: 'block',
            fontSize: 11,
            letterSpacing: 2,
            textTransform: 'uppercase',
            color: 'var(--ocean-text-muted)',
            marginBottom: 6,
            fontFamily: 'var(--ocean-font-mono)',
          }}
        >
          {index}
        </Text>
      ) : null}

      {/* 标题 + 操作区：非对称构图，操作靠右 */}
      <div
        className="ocean-page-intro__head"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'flex-end',
          justifyContent: 'space-between',
          gap: 16,
        }}
      >
        <Title
          level={2}
          style={{
            margin: 0,
            fontSize: 32,
            fontWeight: 650,
            lineHeight: 1.15,
            color: 'var(--ocean-text-primary)',
          }}
        >
          {title}
        </Title>
        {actions ? (
          <div
            className="ocean-page-intro__actions"
            style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}
          >
            {actions}
          </div>
        ) : null}
      </div>

      {/* 用途说明 */}
      {subtitle ? (
        <Paragraph
          className="ocean-page-intro__subtitle"
          style={{
            marginTop: 8,
            marginBottom: 0,
            maxWidth: 720,
            fontSize: 14,
            color: 'var(--ocean-text-secondary)',
          }}
        >
          {subtitle}
        </Paragraph>
      ) : null}

      {/* 附加内容：DataHero / MetricStrip 等 */}
      {children ? (
        <div className="ocean-page-intro__extra" style={{ marginTop: 16 }}>
          {children}
        </div>
      ) : null}
    </section>
  );
}

export default PageIntro;
