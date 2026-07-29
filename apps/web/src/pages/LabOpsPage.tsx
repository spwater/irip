import { useEffect } from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { FlowDetail } from '@/components/FlowDetail';
import { FactsPage } from '@/facts/FactsPage';
import { ParameterPage } from '@/parameters/ParameterPage';
import { usePageHeader } from '@/app/PageHeaderContext';

/** 合法的 Tab key 集合。 */
const VALID_TABS = ['flows', 'facts', 'parameters'] as const;
type LabOpsTab = (typeof VALID_TABS)[number];

/**
 * 实验室运营页面
 *
 * 三个 Tab：实验任务 / 原始数据 / 衍生数据
 * Tab 切换和页面标题注册到 AppShell Header。
 */
export function LabOpsPage(): JSX.Element {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const { setHeader } = usePageHeader();
  const tabRaw = (search as Record<string, unknown>).tab;
  const activeTab: LabOpsTab = (
    VALID_TABS as readonly string[]
  ).includes(typeof tabRaw === 'string' ? tabRaw : '')
    ? (tabRaw as LabOpsTab)
    : 'flows';

  const handleTabChange = (key: string): void => {
    void navigate({ to: '/lab-ops', search: { tab: key }, replace: true });
  };

  useEffect(() => {
    setHeader({
      index: 'MODULE 03 / LAB OPERATIONS',
      title: '实验室运营',
      tabs: [
        { key: 'flows', label: '实验任务' },
        { key: 'facts', label: '原始数据' },
        { key: 'parameters', label: '衍生数据' },
      ],
      activeTab,
      onTabChange: handleTabChange,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  return (
    <div className="ocean-page-enter" key={activeTab}>
      {activeTab === 'flows' && <FlowDetail />}
      {activeTab === 'facts' && <FactsPage />}
      {activeTab === 'parameters' && <ParameterPage />}
    </div>
  );
}
