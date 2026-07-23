import { Card, Tabs, Typography } from 'antd';

const { Title, Paragraph } = Typography;

/**
 * 平台治理页面 — Ant Design Tabs 布局
 *
 * "机构管理"已移至要素管理页面；
 * 保留"审计日志""授权管理"占位 Tab。
 */
export function GovernancePage(): JSX.Element {
  return (
    <Card>
      <Title level={2}>平台治理</Title>
      <Paragraph type="secondary">数据治理与合规管理</Paragraph>
      <Tabs
        defaultActiveKey="audit"
        items={[
          {
            key: 'audit',
            label: '审计日志',
            children: (
              <Paragraph type="secondary">
                审计日志功能将在后续版本提供。
              </Paragraph>
            ),
          },
          {
            key: 'grants',
            label: '授权管理',
            children: (
              <Paragraph type="secondary">
                授权管理功能将在后续版本提供。
              </Paragraph>
            ),
          },
        ]}
      />
    </Card>
  );
}
