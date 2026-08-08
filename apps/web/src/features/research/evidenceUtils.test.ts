import { describe, expect, it } from 'vitest';
import { buildFactGroups, flattenFactIds } from './evidenceUtils';
import type { FactSummary } from '@/api/types';

const baseFacts: FactSummary[] = [
  { fact_id: 'f1', fact_type: 'experiment_run', subject_id: '样品A', status: 'published', task_code: 'T001', task_name: '烧结实验', project_name: '烧结项目', department_name: null, operator: null, run_operator: null, equipment_name: null, data_summary: null, created_at: null },
  { fact_id: 'f2', fact_type: 'experiment_run', subject_id: '样品B', status: 'published', task_code: 'T001', task_name: '烧结实验', project_name: '烧结项目', department_name: null, operator: null, run_operator: null, equipment_name: null, data_summary: null, created_at: null },
  { fact_id: 'f3', fact_type: 'experiment_run', subject_id: '样品C', status: 'published', task_code: 'T002', task_name: '熔炼实验', project_name: '熔炼项目', department_name: null, operator: null, run_operator: null, equipment_name: null, data_summary: null, created_at: null },
];

describe('buildFactGroups', () => {
  it('groups facts by project and task', () => {
    const groups = buildFactGroups(baseFacts, '');
    expect(Object.keys(groups)).toHaveLength(2);
    expect(groups['烧结项目']).toBeDefined();
    expect(groups['烧结项目'].tasks['T001'].facts).toHaveLength(2);
    expect(groups['熔炼项目'].tasks['T002'].facts).toHaveLength(1);
  });

  it('filters by search text matching subject_id', () => {
    const groups = buildFactGroups(baseFacts, '样品A');
    expect(Object.keys(groups)).toHaveLength(1);
    expect(groups['烧结项目'].tasks['T001'].facts).toHaveLength(1);
    expect(groups['烧结项目'].tasks['T001'].facts[0].fact_id).toBe('f1');
  });

  it('filters by search text matching task_name', () => {
    const groups = buildFactGroups(baseFacts, '熔炼');
    expect(Object.keys(groups)).toHaveLength(1);
    expect(groups['熔炼项目']).toBeDefined();
  });

  it('filters by search text matching project_name', () => {
    const groups = buildFactGroups(baseFacts, '烧结项目');
    expect(Object.keys(groups)).toHaveLength(1);
    expect(groups['烧结项目']).toBeDefined();
  });

  it('handles facts with no project or task', () => {
    const noTaskFacts: FactSummary[] = [
      { ...baseFacts[0], project_name: null, task_code: null, task_name: null },
    ];
    const groups = buildFactGroups(noTaskFacts, '');
    expect(groups['未分类项目']).toBeDefined();
    expect(groups['未分类项目'].tasks['未分组']).toBeDefined();
  });

  it('returns empty object for empty facts', () => {
    const groups = buildFactGroups([], '');
    expect(Object.keys(groups)).toHaveLength(0);
  });

  it('is case-insensitive for search', () => {
    const groups = buildFactGroups(baseFacts, 'SINTER');
    expect(Object.keys(groups)).toHaveLength(0);
    // Test Chinese subject search is case insensitive on ASCII
    const groups2 = buildFactGroups(baseFacts, 'A');
    expect(groups2['烧结项目']).toBeDefined();
  });
});

describe('flattenFactIds', () => {
  it('flattens all fact IDs from grouped structure', () => {
    const groups = buildFactGroups(baseFacts, '');
    const ids = flattenFactIds(groups);
    expect(ids).toContain('f1');
    expect(ids).toContain('f2');
    expect(ids).toContain('f3');
    expect(ids).toHaveLength(3);
  });

  it('returns empty array for empty groups', () => {
    const ids = flattenFactIds({});
    expect(ids).toEqual([]);
  });
});
