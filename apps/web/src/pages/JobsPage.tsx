import { Card, Typography } from 'antd';

const { Title, Paragraph } = Typography;

/**
 * 作业中心页面（占位）
 */
export function JobsPage(): JSX.Element {
  return (
    <Card>
      <Title level={2}>作业中心</Title>
      <Paragraph type="secondary">查看与管理所有作业</Paragraph>
    </Card>
  );
}
