import { useNavigate, useSearch } from '@tanstack/react-router';
import { ProjectList } from '@/features/experiment-project/ProjectList';
import { ProjectDetail } from '@/features/experiment-project/ProjectDetail';
import { ParameterPage } from '@/features/parameters/ParameterPage';
import { usePageHeaderRegistration } from '@/app/PageHeaderContext';
import { FeedbackState } from '@/shared/ui';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { ResearchPage } from '@/features/research/ResearchPage';
import { PublicationPage } from '@/features/research/PublicationPage';

/**
 * 实验室运营页面
 *
 * Tab 列表根据功能开关条件渲染：
 * - 研究模块开启：flows / research / publication
 * - 研究模块关闭：flows / parameters / models（原始行为）
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
  const user = useAuthStore((s) => s.user);

  // 功能开关：研究模块是否启用
  const isResearchEnabled = user?.featureFlags?.researchModule ?? false;

  // 根据 功能开关 定义合法 Tab 列表
  const VALID_TABS = isResearchEnabled
    ? (['flows', 'research', 'publication'] as const)
    : (['flows', 'parameters', 'models'] as const);
  type LabOpsTab = (typeof VALID_TABS)[number];

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

  // 衍生数据 Tab 的 provenance_run_id URL 参数（用于深链溯源）
  const provenanceRunIdRaw = (search as Record<string, unknown>).provenance_run_id;
  const provenanceRunId: string | undefined =
    typeof provenanceRunIdRaw === 'string' && provenanceRunIdRaw !== '' ? provenanceRunIdRaw : undefined;

  const handleTabChange = (key: string): void => {
    void navigate({ to: '/lab-ops', search: { tab: key }, replace: true });
  };

  // 根据功能开关定义 Tab 标签
  const tabs = isResearchEnabled
    ? [
        { key: 'flows', label: '实验项目' },
        { key: 'research', label: '研究分析' },
        { key: 'publication', label: '发布成果' },
      ]
    : [
        { key: 'flows', label: '实验项目' },
        { key: 'parameters', label: '衍生数据' },
        { key: 'models', label: '模型发布' },
      ];

  usePageHeaderRegistration(
    {
      index: 'MODULE 03 / LAB OPERATIONS',
      title: '实验室运营',
      tabs,
      activeTab,
      onTabChange: handleTabChange,
    },
    [activeTab, isResearchEnabled],
  );

  return (
    <div className="ocean-page-enter" key={activeTab}>
      {activeTab === 'flows' &&
        (projectId ? <ProjectDetail projectId={projectId} /> : <ProjectList />)}
      {activeTab === 'parameters' && (
        <ParameterPage initialProvenanceRunId={provenanceRunId} />
      )}
      {activeTab === 'models' && (
        <FeedbackState state="empty" title="模型发布" description="开发中，待发布" style={{ padding: 80 }} />
      )}
      {activeTab === 'research' && <ResearchPage />}
      {activeTab === 'publication' && <PublicationPage />}
    </div>
  );
}
