import { describe, expect, it } from 'vitest';
import { buildDeptTree } from './buildDeptTree';
import type { DepartmentListItem } from '@/api/departments';

function makeDept(
  id: string,
  code: string,
  display_name: string,
  parent_id: string | null = null,
): DepartmentListItem {
  return { id, code, display_name, parent_id } as DepartmentListItem;
}

describe('buildDeptTree', () => {
  it('builds flat tree for root-only list', () => {
    const items = [makeDept('d1', 'root', '总机构')];
    const tree = buildDeptTree(items);
    expect(tree).toHaveLength(1);
    expect(tree[0].value).toBe('d1');
    expect(tree[0].title).toBe('公共（总机构）');
    expect(tree[0].selectable).toBe(true);
    expect(tree[0].children).toBeUndefined();
  });

  it('builds two-level tree with parent-child relationship', () => {
    const items = [
      makeDept('d1', 'root', '总机构'),
      makeDept('d2', 'lab_a', '实验室A', 'd1'),
      makeDept('d3', 'lab_b', '实验室B', 'd1'),
    ];
    const tree = buildDeptTree(items);
    expect(tree).toHaveLength(1);
    expect(tree[0].children).toHaveLength(2);
    expect(tree[0].children![0].title).toBe('实验室A');
    expect(tree[0].children![1].title).toBe('实验室B');
  });

  it('handles multiple roots', () => {
    const items = [
      makeDept('d1', 'root', '机构一'),
      makeDept('d2', 'root', '机构二'),
    ];
    const tree = buildDeptTree(items);
    expect(tree).toHaveLength(2);
  });

  it('removes empty children arrays', () => {
    const items = [makeDept('d1', 'root', '总机构')];
    const tree = buildDeptTree(items);
    expect(tree[0].children).toBeUndefined();
  });

  it('handles orphan items (parent_id not in list)', () => {
    const items = [
      makeDept('d1', 'root', '总机构'),
      makeDept('d2', 'orphan', '孤儿部门', 'nonexistent'),
    ];
    const tree = buildDeptTree(items);
    expect(tree).toHaveLength(2);
    const orphan = tree.find((n) => n.value === 'd2');
    expect(orphan).toBeDefined();
    expect(orphan!.title).toBe('孤儿部门');
  });

  it('builds three-level tree', () => {
    const items = [
      makeDept('d1', 'root', '总机构'),
      makeDept('d2', 'lab', '实验室', 'd1'),
      makeDept('d3', 'group', '研究组', 'd2'),
    ];
    const tree = buildDeptTree(items);
    expect(tree).toHaveLength(1);
    expect(tree[0].children).toHaveLength(1);
    expect(tree[0].children![0].children).toHaveLength(1);
    expect(tree[0].children![0].children![0].title).toBe('研究组');
  });

  it('handles empty list', () => {
    expect(buildDeptTree([])).toEqual([]);
  });
});
