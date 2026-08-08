import { describe, expect, it } from 'vitest';
import { buildTree, getDescendantIds, extractErrorMessage } from './departmentUtils';
import type { DepartmentListItem } from '@/api/departments';

const sampleDepartments: DepartmentListItem[] = [
  { id: 'd1', code: 'root', display_name: '总部', description: null, status: 'active', sort_order: 0, member_count: 0, parent_id: null, children_count: 2, equipment_count: 0 },
  { id: 'd2', code: 'lab-a', display_name: '实验室A', description: null, status: 'active', sort_order: 0, member_count: 0, parent_id: 'd1', children_count: 1, equipment_count: 0 },
  { id: 'd3', code: 'lab-b', display_name: '实验室B', description: null, status: 'active', sort_order: 0, member_count: 0, parent_id: 'd1', children_count: 0, equipment_count: 0 },
  { id: 'd4', code: 'group-a1', display_name: 'A组1', description: null, status: 'active', sort_order: 0, member_count: 0, parent_id: 'd2', children_count: 0, equipment_count: 0 },
];

describe('buildTree', () => {
  it('builds a tree with root nodes and nested children', () => {
    const tree = buildTree(sampleDepartments);
    expect(tree).toHaveLength(1);
    expect(tree[0].id).toBe('d1');
    expect(tree[0].children).toHaveLength(2);
    const labA = tree[0].children!.find((c) => c.id === 'd2');
    expect(labA).toBeDefined();
    expect(labA!.children).toHaveLength(1);
    expect(labA!.children![0].id).toBe('d4');
  });

  it('assigns correct levels to nodes', () => {
    const tree = buildTree(sampleDepartments);
    expect(tree[0].level).toBe(0);
    const labA = tree[0].children!.find((c) => c.id === 'd2')!;
    expect(labA.level).toBe(1);
    expect(labA.children![0].level).toBe(2);
  });

  it('handles empty array', () => {
    const tree = buildTree([]);
    expect(tree).toEqual([]);
  });

  it('removes empty children arrays', () => {
    const tree = buildTree(sampleDepartments);
    const labB = tree[0].children!.find((c) => c.id === 'd3')!;
    expect(labB.children).toBeUndefined();
  });
});

describe('getDescendantIds', () => {
  it('returns self and all descendants for root', () => {
    const ids = getDescendantIds(sampleDepartments, 'd1');
    expect(ids.has('d1')).toBe(true);
    expect(ids.has('d2')).toBe(true);
    expect(ids.has('d3')).toBe(true);
    expect(ids.has('d4')).toBe(true);
    expect(ids.size).toBe(4);
  });

  it('returns self and direct children for a lab', () => {
    const ids = getDescendantIds(sampleDepartments, 'd2');
    expect(ids.has('d2')).toBe(true);
    expect(ids.has('d4')).toBe(true);
    expect(ids.has('d1')).toBe(false);
    expect(ids.has('d3')).toBe(false);
  });

  it('returns only self for a leaf node', () => {
    const ids = getDescendantIds(sampleDepartments, 'd4');
    expect(ids.has('d4')).toBe(true);
    expect(ids.size).toBe(1);
  });
});

describe('extractErrorMessage', () => {
  it('extracts message from axios-like error', () => {
    const err = { response: { data: { error: { message: '部门已存在' } } } };
    expect(extractErrorMessage(err)).toBe('部门已存在');
  });

  it('extracts message from Error instance', () => {
    expect(extractErrorMessage(new Error('网络超时'))).toBe('网络超时');
  });

  it('returns default message for unknown error type', () => {
    expect(extractErrorMessage(null)).toBe('操作失败');
    expect(extractErrorMessage(undefined)).toBe('操作失败');
    expect(extractErrorMessage('strange')).toBe('操作失败');
  });

  it('returns default when response has no error.message', () => {
    const err = { response: { data: {} } };
    expect(extractErrorMessage(err)).toBe('操作失败');
  });
});
