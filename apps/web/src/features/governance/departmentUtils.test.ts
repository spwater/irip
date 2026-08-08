import { describe, expect, it } from 'vitest';
import type { DepartmentListItem } from '@/api/departments';
import { buildTree, getDescendantIds, extractErrorMessage } from './departmentUtils';

const makeItem = (id: string, parent_id: string | null = null, display_name = id): DepartmentListItem => ({
  id,
  code: id.toLowerCase(),
  display_name,
  description: null,
  status: 'active',
  sort_order: 0,
  parent_id,
  member_count: 0,
  children_count: 0,
  equipment_count: 0,
});

describe('buildTree', () => {
  it('returns flat list as roots when no parent-child relationships', () => {
    const items = [makeItem('a'), makeItem('b')];
    const tree = buildTree(items);
    expect(tree).toHaveLength(2);
  });

  it('builds parent-child tree structure', () => {
    const items = [makeItem('parent'), makeItem('child', 'parent')];
    const tree = buildTree(items);
    expect(tree).toHaveLength(1);
    expect(tree[0].id).toBe('parent');
    expect(tree[0].children).toHaveLength(1);
    expect(tree[0].children![0].id).toBe('child');
  });

  it('sets child level to parent level + 1', () => {
    const items = [makeItem('parent'), makeItem('child', 'parent')];
    const tree = buildTree(items);
    expect(tree[0].level).toBe(0);
    expect(tree[0].children![0].level).toBe(1);
  });

  it('removes empty children arrays', () => {
    const items = [makeItem('a'), makeItem('b')];
    const tree = buildTree(items);
    expect(tree[0].children).toBeUndefined();
  });

  it('handles multi-level tree', () => {
    const items = [
      makeItem('root'),
      makeItem('mid', 'root'),
      makeItem('leaf', 'mid'),
    ];
    const tree = buildTree(items);
    expect(tree).toHaveLength(1);
    expect(tree[0].children![0].children![0].id).toBe('leaf');
    expect(tree[0].children![0].children![0].level).toBe(2);
  });
});

describe('getDescendantIds', () => {
  it('returns set containing just the id when no children', () => {
    const items = [makeItem('a')];
    const result = getDescendantIds(items, 'a');
    expect(result.has('a')).toBe(true);
    expect(result.size).toBe(1);
  });

  it('returns all descendant ids including self', () => {
    const items = [
      makeItem('root'),
      makeItem('child1', 'root'),
      makeItem('child2', 'root'),
      makeItem('grandchild', 'child1'),
    ];
    const result = getDescendantIds(items, 'root');
    expect(result.has('root')).toBe(true);
    expect(result.has('child1')).toBe(true);
    expect(result.has('child2')).toBe(true);
    expect(result.has('grandchild')).toBe(true);
    expect(result.size).toBe(4);
  });

  it('does not include siblings', () => {
    const items = [
      makeItem('parent'),
      makeItem('a', 'parent'),
      makeItem('b', 'parent'),
    ];
    const result = getDescendantIds(items, 'a');
    expect(result.has('a')).toBe(true);
    expect(result.has('b')).toBe(false);
    expect(result.has('parent')).toBe(false);
  });
});

describe('extractErrorMessage', () => {
  it('extracts message from axios response', () => {
    const error = { response: { data: { error: { message: 'Validation failed' } } } };
    expect(extractErrorMessage(error)).toBe('Validation failed');
  });

  it('falls back to Error.message when no response', () => {
    expect(extractErrorMessage(new Error('network down'))).toBe('network down');
  });

  it('falls back to 操作失败 for unknown error types', () => {
    expect(extractErrorMessage('something')).toBe('操作失败');
    expect(extractErrorMessage(null)).toBe('操作失败');
  });

  it('falls back to 操作失败 when response has no error.message', () => {
    const error = { response: { data: {} } };
    expect(extractErrorMessage(error)).toBe('操作失败');
  });
});
