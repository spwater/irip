/**
 * 部门树构建工具 — 将扁平部门列表构建为 Ant Design TreeSelect 的 treeData 格式。
 *
 * root 哨兵显示为"公共（{机构名}）"，其他按 display_name 显示。
 * 用于实验项目/设备/实验对象的"可见单位"树形多选下拉。
 */
import type { DepartmentListItem } from '@/api/departments';

export type DeptTreeNode = {
  value: string;
  title: string;
  selectable: boolean;
  children?: DeptTreeNode[];
};

export function buildDeptTree(items: DepartmentListItem[]): DeptTreeNode[] {
  const map = new Map<string, DeptTreeNode>();
  const roots: DeptTreeNode[] = [];
  for (const item of items) {
    const isRoot = item.code === 'root';
    map.set(item.id, {
      value: item.id,
      title: isRoot ? `公共（${item.display_name}）` : item.display_name,
      selectable: true,
      children: [],
    });
  }
  for (const item of items) {
    const node = map.get(item.id)!;
    if (item.parent_id && map.has(item.parent_id)) {
      map.get(item.parent_id)!.children!.push(node);
    } else {
      roots.push(node);
    }
  }
  const cleanup = (node: DeptTreeNode) => {
    if (node.children && node.children.length === 0) {
      delete node.children;
    } else if (node.children) {
      node.children.forEach(cleanup);
    }
  };
  roots.forEach(cleanup);
  return roots;
}
