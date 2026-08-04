import { useNavigate, useSearch } from '@tanstack/react-router';
import { ProjectList } from '@/features/experiment-project/ProjectList';
import { ProjectDetail } from '@/features/experiment-project/ProjectDetail';
import { ParameterPage } from '@/features/parameters/ParameterPage';
import { usePageHeaderRegistration } from '@/app/PageHeaderContext';
import { FeedbackState } from '@/shared/ui';

/** 合法的 Tab key 集合。 */
const VALID_TABS = ['flows', 'parameters', 'models'] as const;
type LabOpsTab = (typeof VALID_TABS)[number];

/**
 * 实验室运营页面
 *
 * 三个 Tab：实验项目 / 衍生数据 / 模型发布
 * Tab 切换和页面标题注册到 AppShell Header。
 *
 * flows Tab（实验项目）：
 * - 读 URL 参数 project，有值渲染 ProjectDetail，无值渲染 ProjectList。
 * - 支持 ?project={project_id} 深链直达项目详情。
 *
 * M-09 整改：使用 usePageHeaderRegistration，unmount 时清空 header。
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

  // 实验项目 Tab 的 project URL 参数
  const projectRaw = (search as Record<string, unknown>).project;
  const projectId: string | undefined =
    typeof projectRaw === 'string' && projectRaw !== '' ? projectRaw : undefined;

  const handleTabChange = (key: string): void => {
    void navigate({ to: '/lab-ops', search: { tab: key }, replace: true });
  };

  usePageHeaderRegistration(
    {
      index: 'MODULE 03 / LAB OPERATIONS',
      title: '实验室运营',
      tabs: [
        { key: 'flows', label: '实验项目' },
        { key: 'parameters', label: '衍生数据' },
        { key: 'models', label: '模型发布' },
      ],
      activeTab,
      onTabChange: handleTabChange,
    },
    [activeTab],
  );

  return (
    <div className="ocean-page-enter" key={activeTab}>
      {activeTab === 'flows' &&
        (projectId ? <ProjectDetail projectId={projectId} /> : <ProjectList />)}
      {activeTab === 'parameters' && <ParameterPage />}
      {activeTab === 'models' && (
        <FeedbackState state="empty" title="模型发布" description="开发中，待发布" style={{ padding: 80 }} />
      )}
    </div>
  );
}
