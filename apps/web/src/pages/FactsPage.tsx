import { Card, Typography } from 'antd';

const { Title, Paragraph } = Typography;

/**
 * 实验事实页面（占位）
 */
export function FactsPage(): JSX.Element {
  return (
    <Card>
      <Title level={2}>实验事实</Title>
      <Paragraph type="secondary">管理实验数据与事实记录</Paragraph>
    </Card>
  );
}
