import { Tabs, Typography } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { FlowDetail } from '@/components/FlowDetail';
import { FactsPage } from '@/facts/FactsPage';
import { ParameterPage } from '@/parameters/ParameterPage';

const { Title } = Typography;

/** 合法的 Tab key 集合。 */
const VALID_TABS = ['flows', 'facts', 'parameters'] as const;
type LabOpsTab = (typeof VALID_TABS)[number];

/**
 * 实验室运营页面
 *
 * 三个 Tab：实验执行 / 实验记录 / 数据抽取
 *
 * Tab 状态由 URL search param `tab` 驱动（source of truth），
 * 这样从详情页（如事实详情 `/facts/$factId`）的「返回列表」按钮
 * 可通过 `/lab-ops?tab=facts` 深链回「实验记录」Tab，
 * 避免重定向到默认 Tab 丢失上下文。
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
    // 切换 Tab 仅替换当前历史记录，避免污染浏览器后退栈
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
