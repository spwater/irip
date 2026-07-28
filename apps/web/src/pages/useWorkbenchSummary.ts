/**
 * useWorkbenchSummary — 研发看板摘要查询
 *
 * 5 个独立的 TanStack Query，各自管理 loading/error/success 状态。
 * 一个面板失败不影响其他面板的数据展示（partial resilience）。
 * 不将查询结果归一化为第二个状态抽象；调用方直接读取各自的 UseQueryResult。
 *
 * 产出：
 * - facts:  事实列表（含 group_counts 分组统计）
 * - flows:  流程列表
 * - models: 模型列表
 * - jobs:   最近作业（limit=8）
 * - health: 系统健康
 * - factCount: 从 facts.group_counts 汇总的事实条数
 */
import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { apiListFacts } from '@/api/facts';
import { apiListFlows } from '@/api/flows';
import { apiListModels } from '@/api/models';
import { apiListJobs } from '@/api/jobs';
import { apiGetSystemHealth } from '@/api/system';
import type {
  CursorPage,
  FactListResult,
  FlowSummary,
  ModelSummary,
  JobListResponse,
  SystemHealth,
} from '@/api/client';

export type WorkbenchSummary = {
  facts: UseQueryResult<FactListResult>;
  flows: UseQueryResult<CursorPage<FlowSummary>>;
  models: UseQueryResult<CursorPage<ModelSummary>>;
  jobs: UseQueryResult<JobListResponse>;
  health: UseQueryResult<SystemHealth>;
  factCount: number;
};

/**
 * 获取研发看板摘要数据。
 *
 * 每个 query 独立运行，互不阻塞。factCount 从 facts.group_counts 汇总，
 * 若 group_counts 不存在则返回 0（不虚构总数）。
 */
export function useWorkbenchSummary(): WorkbenchSummary {
  const facts = useQuery({
    queryKey: ['workbench', 'facts'],
    queryFn: () => apiListFacts({ page_size: 12 }),
  });

  const flows = useQuery({
    queryKey: ['workbench', 'flows'],
    queryFn: () => apiListFlows(),
  });

  const models = useQuery({
    queryKey: ['workbench', 'models'],
    queryFn: () => apiListModels(),
  });

  const jobs = useQuery({
    queryKey: ['workbench', 'jobs'],
    queryFn: () => apiListJobs({ limit: 8 }),
  });

  const health = useQuery({
    queryKey: ['system-health'],
    queryFn: apiGetSystemHealth,
  });

  // 从 group_counts 汇总事实条数；若字段不存在则返回 0，不虚构
  const factCount: number = Object.values(
    facts.data?.group_counts ?? {},
  ).reduce((sum: number, count: number) => sum + count, 0);

  return { facts, flows, models, jobs, health, factCount };
}
