import { describe, expect, it } from 'vitest';
import { buildFactGroups, flattenFactIds } from './factGroups';
import type { FactSummary } from '@/api/types';

function makeFact(
  fact_id: string,
  subject_id: string,
  project_name?: string,
  task_code?: string,
  task_name?: string,
): FactSummary {
  return {
    fact_id,
    subject_id,
    project_name: project_name ?? null,
    task_code: task_code ?? null,
    task_name: task_name ?? null,
  } as FactSummary;
}

describe('buildFactGroups', () => {
  it('groups facts by project then task', () => {
    const facts = [
      makeFact('f1', 's1', 'Project A', 'T-001', 'Task 1'),
      makeFact('f2', 's2', 'Project A', 'T-001', 'Task 1'),
      makeFact('f3', 's3', 'Project A', 'T-002', 'Task 2'),
      makeFact('f4', 's4', 'Project B', 'T-003', 'Task 3'),
    ];

    const groups = buildFactGroups(facts, '');

    expect(Object.keys(groups)).toHaveLength(2);
    expect(groups['Project A'].projectName).toBe('Project A');
    expect(Object.keys(groups['Project A'].tasks)).toHaveLength(2);
    expect(groups['Project A'].tasks['T-001'].facts).toHaveLength(2);
    expect(groups['Project A'].tasks['T-002'].facts).toHaveLength(1);
    expect(groups['Project B'].tasks['T-003'].facts).toHaveLength(1);
  });

  it('uses 未分类项目 for facts without project_name', () => {
    const facts = [makeFact('f1', 's1', undefined, 'T-001', 'Task 1')];
    const groups = buildFactGroups(facts, '');
    expect(groups['未分类项目']).toBeDefined();
  });

  it('uses 未分组 for facts without task_code', () => {
    const facts = [makeFact('f1', 's1', 'Project A', undefined, undefined)];
    const groups = buildFactGroups(facts, '');
    expect(groups['Project A'].tasks['未分组']).toBeDefined();
    expect(groups['Project A'].tasks['未分组'].taskName).toBe('未分组');
  });

  it('filters by search text on subject_id, task_name, and project_name', () => {
    const facts = [
      makeFact('f1', 'alpha-sample', 'Project A', 'T-001', 'Task Alpha'),
      makeFact('f2', 'beta-sample', 'Project B', 'T-002', 'Task Beta'),
      makeFact('f3', 'gamma-sample', 'Project Alpha', 'T-003', 'Task Gamma'),
    ];

    const groups = buildFactGroups(facts, 'alpha');
    // Should match subject_id 'alpha-sample' and project_name 'Project Alpha'
    const allFacts = Object.values(groups).flatMap((p) =>
      Object.values(p.tasks).flatMap((t) => t.facts),
    );
    expect(allFacts).toHaveLength(2);
    expect(allFacts.map((f) => f.fact_id).sort()).toEqual(['f1', 'f3']);
  });

  it('search is case-insensitive', () => {
    const facts = [
      makeFact('f1', 'UPPER', 'Project X', 'T-001', 'Task 1'),
      makeFact('f2', 'lower', 'Project X', 'T-002', 'Task 2'),
    ];

    const groups = buildFactGroups(facts, 'UPPER');
    const allFacts = Object.values(groups).flatMap((p) =>
      Object.values(p.tasks).flatMap((t) => t.facts),
    );
    expect(allFacts).toHaveLength(1);
    expect(allFacts[0].fact_id).toBe('f1');
  });

  it('returns empty groups for empty facts', () => {
    const groups = buildFactGroups([], '');
    expect(Object.keys(groups)).toHaveLength(0);
  });
});

describe('flattenFactIds', () => {
  it('flattens all fact_ids from nested groups', () => {
    const facts = [
      makeFact('f1', 's1', 'Project A', 'T-001', 'Task 1'),
      makeFact('f2', 's2', 'Project A', 'T-002', 'Task 2'),
      makeFact('f3', 's3', 'Project B', 'T-003', 'Task 3'),
    ];
    const groups = buildFactGroups(facts, '');
    const ids = flattenFactIds(groups);
    expect(ids.sort()).toEqual(['f1', 'f2', 'f3']);
  });

  it('returns empty array for empty groups', () => {
    expect(flattenFactIds({})).toEqual([]);
  });
});
