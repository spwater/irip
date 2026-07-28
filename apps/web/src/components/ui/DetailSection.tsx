/**
 * DetailSection — 详情区段
 *
 * 统一详情页中各区段的标题 + 额外操作 + 内容结构。
 * technical=true 时内容区使用等宽字体（ocean-tech）。
 */
import type { PropsWithChildren, ReactNode } from 'react';
import { Typography } from 'antd';

const { Title } = Typography;

export type DetailSectionProps = PropsWithChildren<{
  title: string;
  extra?: ReactNode;
  technical?: boolean;
}>;

export function DetailSection({
  title,
  extra,
  technical = false,
  children,
}: DetailSectionProps): JSX.Element {
  const sectionClass = `ocean-detail-section${technical ? ' ocean-detail-section--technical' : ''}`;
  return (
    <section className={sectionClass}>
      <header className="ocean-detail-section__header">
        <Title level={5} style={{ marginBottom: 0 }}>
          {title}
        </Title>
        {extra ? <div className="ocean-detail-section__extra">{extra}</div> : null}
      </header>
      <div className={technical ? 'ocean-tech' : 'ocean-detail-section__body'}>{children}</div>
    </section>
  );
}
