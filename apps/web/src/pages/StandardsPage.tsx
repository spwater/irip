import { Card, Typography } from 'antd';

const { Title, Paragraph } = Typography;

/**
 * 标准管理页面（占位）
 */
export function StandardsPage(): JSX.Element {
  return (
    <Card>
      <Title level={2}>标准管理</Title>
      <Paragraph type="secondary">管理行业标准与规范</Paragraph>
    </Card>
  );
}
