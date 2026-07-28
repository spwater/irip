import { Tabs } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { FlowDetail } from '@/components/FlowDetail';
import { FactsPage } from '@/facts/FactsPage';
import { ParameterPage } from '@/parameters/ParameterPage';
import { PageIntro } from '@/components/ui';

/** 合法的 Tab key 集合。 */
const VALID_TABS = ['flows', 'facts', 'parameters'] as const;
type LabOpsTab = (typeof VALID_TABS)[number];

/**
 * 实验室运营页面（设计文档第 10.5 节）
 *
 * 三个 Tab：实验任务 / 原始数据 / 衍生数据
 * 使用 PageIntro 统一标题区。
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
    <div className="ocean-page-enter">
      <PageIntro
        index="MODULE 03 / LAB OPERATIONS"
        title="实验室运营"
        subtitle="管理实验执行流程、原始数据记录与衍生数据参数。"
      />
      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={[
          {
            key: 'flows',
            label: '实验任务',
            children: <FlowDetail />,
          },
          {
            key: 'facts',
            label: '原始数据',
            children: <FactsPage />,
          },
          {
            key: 'parameters',
            label: '衍生数据',
            children: <ParameterPage />,
          },
        ]}
      />
    </div>
  );
}
