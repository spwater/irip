import { Card, Typography } from 'antd';

const { Title, Paragraph } = Typography;

/**
 * 研发看板页面（占位）
 */
export function WorkbenchPage(): JSX.Element {
  return (
    <Card>
      <Title level={2}>研发看板</Title>
      <Paragraph type="secondary">IRIP 智能研发集成平台 — 研发看板首页</Paragraph>
    </Card>
  );
}
