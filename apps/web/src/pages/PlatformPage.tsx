import { Tabs, Typography } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { AssistantPage } from '@/assistant/AssistantPage';
import { ParameterPage } from '@/parameters/ParameterPage';

const { Title } = Typography;

const VALID_TABS = ['assistant', 'parameters'] as const;
type PlatformTab = (typeof VALID_TABS)[number];

/**
 * 平台应用页面
 *
 * 两个 Tab：AI 助手 / 数据抽取
 */
export function PlatformPage(): JSX.Element {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const tabRaw = (search as Record<string, unknown>).tab;
  const activeTab: PlatformTab = (
    VALID_TABS as readonly string[]
  ).includes(typeof tabRaw === 'string' ? tabRaw : '')
    ? (tabRaw as PlatformTab)
    : 'assistant';

  const handleTabChange = (key: string): void => {
    void navigate({ to: '/platform', search: { tab: key }, replace: true });
  };

  return (
    <div>
      <Title level={2}>平台应用</Title>
      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={[
          {
            key: 'assistant',
            label: 'AI 助手',
            children: <AssistantPage />,
          },
          {
            key: 'parameters',
            label: '数据抽取',
            children: <ParameterPage />,
          },
        ]}
      />
    </div>
  );
}
