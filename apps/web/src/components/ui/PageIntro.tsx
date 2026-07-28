/**
 * PageIntro — 页面引导区
 *
 * 渲染页面级标题（h1）、索引标签、描述文字和操作插槽。
 * 仅做展示，不持有业务状态。
 */
import type { ReactNode } from 'react';
import { Typography } from 'antd';

const { Title, Paragraph } = Typography;

export type PageIntroProps = {
  /** 索引标签，如 "LAB / 01" */
  index?: string;
  /** 页面标题（渲染为 h1） */
  title: string;
  /** 页面描述 */
  description?: string;
  /** 操作按钮插槽 */
  actions?: ReactNode;
  /** 额外内容 */
  children?: ReactNode;
};

export function PageIntro({
  index,
  title,
  description,
  actions,
  children,
}: PageIntroProps): JSX.Element {
  return (
    <header className="ocean-page-intro">
      <div className="ocean-page-intro__head">
        <div className="ocean-page-intro__heading">
          {index ? <span className="ocean-index">{index}</span> : null}
          <Title level={1} style={{ marginBottom: 0 }}>
            {title}
          </Title>
          {description ? (
            <Paragraph type="secondary" style={{ marginBottom: 0 }}>
              {description}
            </Paragraph>
          ) : null}
        </div>
        {actions ? <div className="ocean-page-intro__actions">{actions}</div> : null}
      </div>
      {children ? <div className="ocean-page-intro__body">{children}</div> : null}
    </header>
  );
}
