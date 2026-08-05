/**
 * 事实分组纯函数 — 从 AssistantPage.tsx 提取。
 *
 * 按项目→任务分组样品，支持搜索过滤（三层树：项目→任务→数据）。
 */

import type { FactSummary } from '@/api/types';
import type { FactGroups } from '../types';

/**
 * 按项目→任务三层结构分组事实列表。
 *
 * @param allFacts 全部事实列表
 * @param searchText 搜索关键词（为空则不过滤）
 * @returns 按 projectKey → { projectName, tasks: { [taskCode]: { taskName, facts } } } 分组
 */
export function buildFactGroups(allFacts: FactSummary[], searchText: string): FactGroups {
  const filtered = searchText.trim()
    ? allFacts.filter((f) =>
        f.subject_id.toLowerCase().includes(searchText.toLowerCase()) ||
        (f.task_name ?? '').toLowerCase().includes(searchText.toLowerCase()) ||
        (f.project_name ?? '').toLowerCase().includes(searchText.toLowerCase()),
      )
    : allFacts;

  const projects: FactGroups = {};
  for (const f of filtered) {
    const projKey = f.project_name ?? '未分类项目';
    const taskKey = f.task_code ?? '未分组';
    if (!projects[projKey]) projects[projKey] = { projectName: projKey, tasks: {} };
    if (!projects[projKey].tasks[taskKey]) {
      projects[projKey].tasks[taskKey] = { taskName: f.task_name ?? f.task_code ?? '未分组', facts: [] };
    }
    projects[projKey].tasks[taskKey].facts.push(f);
  }
  return projects;
}

/**
 * 从 factGroups 中展开所有 fact_id。
 */
export function flattenFactIds(groups: FactGroups): string[] {
  return Object.values(groups).flatMap((p) =>
    Object.values(p.tasks).flatMap((t) => t.facts.map((f) => f.fact_id)),
  );
}
