import { Card, Typography } from 'antd';

const { Title, Paragraph } = Typography;

/**
 * 参数管理页面（占位）
 */
export function ParametersPage(): JSX.Element {
  return (
    <Card>
      <Title level={2}>参数管理</Title>
      <Paragraph type="secondary">管理系统参数与配置</Paragraph>
    </Card>
  );
}
