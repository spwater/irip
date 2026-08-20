/**
 * EvidencePanel 辅助函数 — 从主组件提取（P2-C22）。
 *
 * 树形分组构建、扁平化 ID 提取。
 */

import type { FactSummary } from '@/api/types';

// ---- 树形分组类型 ----
export type FactItem = {
  fact_id: string;
  subject_id: string;
  equipment_name?: string | null;
  run_operator?: string | null;
  data_summary?: string | null;
};
export type TaskGroup = { taskName: string; facts: FactItem[] };
export type ProjectGroup = { projectName: string; tasks: Record<string, TaskGroup> };
export type FactGroups = Record<string, ProjectGroup>;

/**
 * 将扁平 Fact 列表按项目→任务分组为树形结构，支持搜索过滤。
 */
export function buildFactGroups(
  allFacts: FactSummary[],
  searchText: string,
): FactGroups {
  const filtered = searchText.trim()
    ? allFacts.filter(
        (f) =>
          f.subject_id.toLowerCase().includes(searchText.toLowerCase()) ||
          (f.task_name ?? '').toLowerCase().includes(searchText.toLowerCase()) ||
          (f.project_name ?? '').toLowerCase().includes(searchText.toLowerCase()),
      )
    : allFacts;

  const groups: FactGroups = {};
  for (const f of filtered) {
    const projKey = f.project_name ?? '未分类项目';
    const taskKey = f.task_code ?? '未分组';
    if (!groups[projKey]) groups[projKey] = { projectName: projKey, tasks: {} };
    if (!groups[projKey].tasks[taskKey]) {
      groups[projKey].tasks[taskKey] = { taskName: f.task_name ?? taskKey, facts: [] };
    }
    groups[projKey].tasks[taskKey].facts.push({
      fact_id: f.fact_id,
      subject_id: f.subject_id,
      equipment_name: f.equipment_name,
      run_operator: f.run_operator,
      data_summary: f.data_summary,
    });
  }
  return groups;
}

/**
 * 将树形分组扁平化为 fact_id 数组。
 */
export function flattenFactIds(groups: FactGroups): string[] {
  return Object.values(groups).flatMap((p) =>
    Object.values(p.tasks).flatMap((t) => t.facts.map((f) => f.fact_id)),
  );
}
