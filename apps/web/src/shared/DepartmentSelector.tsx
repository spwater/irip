/**
 * 部门树选择器（可复用）
 *
 * 从 /api/v1/departments 获取部门树，使用 Ant Design TreeSelect 渲染。
 * - root 显示为"公共（{机构名}）"
 * - 多部门用户可见选择器，单部门用户隐藏（自动用 primary）
 * - 普通用户禁选 root（仅管理员可挂 root），通过 allowRoot props 控制
 */
import { useMemo } from 'react';
import { TreeSelect } from 'antd';
import type { TreeSelectProps } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { apiListDepartments, type DepartmentListItem } from '@/api/departments';

/** 树节点类型 */
type DeptTreeNode = {
  value: string;
  title: string;
  selectable: boolean;
  children?: DeptTreeNode[];
};

/** 将扁平列表构建为树形结构 */
function buildDeptTree(items: DepartmentListItem[], allowRoot: boolean): DeptTreeNode[] {
  const map = new Map<string, DeptTreeNode>();
  const roots: DeptTreeNode[] = [];

  const filtered = items;

  // 创建节点
  for (const item of filtered) {
    const isRoot = item.code === 'root';
    const title = isRoot ? `公共（${item.display_name}）` : item.display_name;
    map.set(item.id, {
      value: item.id,
      title,
      selectable: isRoot ? allowRoot : true,
      children: [],
    });
  }

  // 构建父子关系
  for (const item of filtered) {
    const node = map.get(item.id)!;
    if (item.parent_id && map.has(item.parent_id)) {
      map.get(item.parent_id)!.children!.push(node);
    } else {
      roots.push(node);
    }
  }

  // 清理空 children
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

/** DepartmentSelector 组件 Props */
export type DepartmentSelectorProps = {
  /** 选中值 */
  value?: string;
  /** 选中变化回调 */
  onChange?: (value: string | undefined) => void;
  /** placeholder */
  placeholder?: string;
  /** 是否允许选择 root 哨兵（仅管理员） */
  allowRoot?: boolean;
  /** 是否禁用 */
  disabled?: boolean;
  /** 样式 */
  style?: React.CSSProperties;
};

/**
 * 部门树选择器组件
 *
 * 从 API 获取部门列表，构建树形结构，使用 Ant Design TreeSelect 渲染。
 * root 哨兵显示为"公共（{名称}）"。
 * 普通用户禁选 root（通过 allowRoot=false 控制）。
 */
export function DepartmentSelector({
  value,
  onChange,
  placeholder = '选择归属部门',
  allowRoot = false,
  disabled = false,
  style,
}: DepartmentSelectorProps): JSX.Element {
  const { data, isLoading } = useQuery({
    queryKey: ['departments-for-selector'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });

  const treeData = useMemo(
    () => buildDeptTree(data?.items ?? [], allowRoot),
    [data, allowRoot],
  );

  const treeSelectProps: TreeSelectProps = {
    value,
    onChange,
    placeholder,
    disabled: disabled || isLoading,
    treeData,
    treeDefaultExpandAll: true,
    showSearch: true,
    treeNodeFilterProp: 'title',
    allowClear: true,
    style: { width: '100%', ...style },
  };

  return <TreeSelect {...treeSelectProps} />;
}
