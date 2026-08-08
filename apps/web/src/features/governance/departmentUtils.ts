/**
 * DepartmentManagement 辅助函数 — 从主组件提取（P2-C22）。
 *
 * 树形构建、后代 ID 查找、错误消息提取。
 */

import type { DepartmentListItem } from '@/api/departments';

/**
 * 树形节点类型：DepartmentListItem + children 数组。
 */
export type DepartmentTreeNode = DepartmentListItem & {
  children?: DepartmentTreeNode[];
  level?: number;
};

/**
 * 将扁平列表构建为树形结构。
 */
export function buildTree(items: DepartmentListItem[]): DepartmentTreeNode[] {
  const map = new Map<string, DepartmentTreeNode>();
  items.forEach((item) => map.set(item.id, { ...item, children: [], level: 0 }));
  const roots: DepartmentTreeNode[] = [];
  map.forEach((item) => {
    if (item.parent_id && map.has(item.parent_id)) {
      const parent = map.get(item.parent_id)!;
      item.level = (parent.level ?? 0) + 1;
      parent.children!.push(item);
    } else {
      roots.push(item);
    }
  });
  map.forEach((item) => {
    if (item.children && item.children.length === 0) {
      delete item.children;
    }
  });
  return roots;
}

/**
 * 获取指定部门的所有后代 ID（含自身）。
 * 用于编辑时排除自己及子孙作为上级选项，防止循环引用。
 */
export function getDescendantIds(
  items: DepartmentListItem[],
  id: string,
): Set<string> {
  const result = new Set<string>([id]);
  const queue = [id];
  while (queue.length > 0) {
    const current = queue.shift()!;
    items.forEach((item) => {
      if (item.parent_id === current && !result.has(item.id)) {
        result.add(item.id);
        queue.push(item.id);
      }
    });
  }
  return result;
}

/**
 * 从未知错误中提取消息字符串。
 * 支持 Axios 响应格式 {response: {data: {error: {message}}}}。
 */
export function extractErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const response = (err as { response?: { data?: { error?: { message?: string } } } }).response;
    if (response?.data?.error?.message) {
      return response.data.error.message;
    }
  }
  if (err instanceof Error) {
    return err.message;
  }
  return '操作失败';
}
