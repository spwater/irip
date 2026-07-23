import { Card, Typography } from 'antd';

const { Title, Paragraph } = Typography;

/**
 * 实验室配置页面（占位）
 */
export function StandardsPage(): JSX.Element {
  return (
    <Card>
      <Title level={2}>实验室配置</Title>
      <Paragraph type="secondary">管理行业标准与规范</Paragraph>
    </Card>
  );
}
