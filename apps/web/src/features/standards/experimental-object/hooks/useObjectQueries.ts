/**
 * useObjectQueries — ExperimentalObjectPage 所有 useQuery 声明 + 派生数据。
 *
 * 从 ExperimentalObjectPage.tsx 提取。统一管理所有数据查询和派生映射，
 * 供主组件和子组件使用。
 */

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  apiListObjects,
  apiListObjectTypes,
  type ObjectTypeDictItem,
} from '@/api/standards-objects';
import { apiGetDepartmentNameMap, apiListDepartments } from '@/api/departments';
import { apiListComponents } from '@/api/equipment-flows';
import { apiListIngestionTools } from '@/api/models-ai';
import { buildDeptTree, type DeptTreeNode } from '@/shared/buildDeptTree';
import type { IndustrialObject } from '@/api/types';

export interface UseObjectQueriesResult {
  // 实验对象类型字典
  objectTypeData: ObjectTypeDictItem[] | undefined;
  objectTypeOptions: { value: string; label: string }[];
  allTypeCodes: string;
  // 实验对象列表
  items: IndustrialObject[];
  isLoading: boolean;
  // 部门
  deptMap: Map<string, string>;
  deptOptions: { value: string; label: string }[];
  allDeptOptions: { value: string; label: string }[];
  deptTreeData: DeptTreeNode[];
  // 数据接口
  componentOptions: { value: string; label: string }[];
  componentMap: Map<string, string>;
  // 解析工具
  ingestionToolOptions: { value: string; label: string }[];
}

export function useObjectQueries(): UseObjectQueriesResult {
  // 动态加载类型字典
  const { data: objectTypeData } = useQuery({
    queryKey: ['object-types'],
    queryFn: apiListObjectTypes,
  });
  const objectTypeOptions = (objectTypeData ?? []).map((t) => ({
    value: t.code,
    label: t.display_name,
  }));

  // ---- 数据查询：始终拿全部类型的数据，前端按 typeFilter 过滤 ----
  const allTypeCodes =
    (objectTypeData ?? []).map((t) => t.code).join(',') || 'material,signal';
  const { data, isLoading } = useQuery({
    queryKey: ['exp-objects', allTypeCodes],
    queryFn: () =>
      apiListObjects({
        object_type: allTypeCodes,
        page_size: 100,
      }),
  });

  const items: IndustrialObject[] = data?.items ?? [];

  // 全部门名称映射（不受部门隔离限制），用于所属单位/可见单位列名称展示
  const { data: deptNameMapData } = useQuery({
    queryKey: ['department-name-map'],
    queryFn: apiGetDepartmentNameMap,
  });
  const deptMap = new Map(
    (deptNameMapData ?? []).map((d) => [d.id, d.display_name]),
  );

  const { data: deptData } = useQuery({
    queryKey: ['departments-for-object-filter'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptOptions = (deptData?.items ?? []).map((d) => ({
    value: d.id,
    label: d.display_name,
  }));

  // 全部部门选项（用于所属单位 + 可见单位选择）
  const allDeptOptions = (deptData?.items ?? []).map((d) => ({
    value: d.id,
    label: d.display_name,
  }));

  // 部门树数据（用于可见单位树形多选）
  const deptTreeData = useMemo(
    () => buildDeptTree(deptData?.items ?? []),
    [deptData],
  );

  // ---- 数据接口列表查询（用于下拉选择与列表展示）----
  const { data: componentData } = useQuery({
    queryKey: ['components'],
    queryFn: () => apiListComponents(),
  });
  const componentOptions = (componentData?.items ?? []).map((c) => ({
    value: c.id,
    label: c.display_name || c.name,
  }));
  const componentMap = new Map(
    (componentData?.items ?? []).map((c) => [c.id, c.display_name || c.name]),
  );

  // ---- 解析工具列表（用于新建接口表单）----
  const { data: ingestionToolsData } = useQuery({
    queryKey: ['ingestion-tools'],
    queryFn: apiListIngestionTools,
  });
  const ingestionToolOptions = (ingestionToolsData ?? []).map((t) => ({
    value: t.name,
    label: t.display_name,
  }));

  return {
    objectTypeData,
    objectTypeOptions,
    allTypeCodes,
    items,
    isLoading,
    deptMap,
    deptOptions,
    allDeptOptions,
    deptTreeData,
    componentOptions,
    componentMap,
    ingestionToolOptions,
  };
}
