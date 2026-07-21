import { Card, Typography } from 'antd';

const { Title, Paragraph } = Typography;

/**
 * 模型管理页面（占位）
 */
export function ModelsPage(): JSX.Element {
  return (
    <Card>
      <Title level={2}>模型管理</Title>
      <Paragraph type="secondary">管理 AI 模型与版本</Paragraph>
    </Card>
  );
}
