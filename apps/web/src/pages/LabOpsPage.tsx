import { Tabs, Typography } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { FlowDetail } from '@/components/FlowDetail';
import { FactsPage } from '@/facts/FactsPage';

const { Title } = Typography;

/** 合法的 Tab key 集合。 */
const VALID_TABS = ['flows', 'facts'] as const;
type LabOpsTab = (typeof VALID_TABS)[number];

/**
 * 实验室运营页面
 *
 * 两个 Tab：实验执行 / 实验记录
 */
export function LabOpsPage(): JSX.Element {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const tabRaw = (search as Record<string, unknown>).tab;
  const activeTab: LabOpsTab = (
    VALID_TABS as readonly string[]
  ).includes(typeof tabRaw === 'string' ? tabRaw : '')
    ? (tabRaw as LabOpsTab)
    : 'flows';

  const handleTabChange = (key: string): void => {
    void navigate({ to: '/lab-ops', search: { tab: key }, replace: true });
  };

  return (
    <div>
      <Title level={2}>实验室运营</Title>
      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={[
          {
            key: 'flows',
            label: '实验执行',
            children: <FlowDetail />,
          },
          {
            key: 'facts',
            label: '实验记录',
            children: <FactsPage />,
          },
        ]}
      />
    </div>
  );
}
