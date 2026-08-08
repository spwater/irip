/**
 * useFlowQueries — FlowDetail 所有 useQuery 声明 + 派生数据。
 *
 * 从 FlowDetail.tsx 提取。统一管理所有数据查询和派生映射，
 * 供主组件和子组件使用。
 */

import { useEffect, useMemo } from 'react';
import { Form } from 'antd';
import { useQuery } from '@tanstack/react-query';
import {
  apiListComponents,
  apiListEquipment,
  apiListFlows,
  apiListFlowRuns,
  apiGetFlow,
  type ComponentSummary,
  type FlowNodeSchema,
  type FlowRunSummary,
  type FlowSummary,
} from '@/api/equipment-flows';
import { apiListIngestionTools } from '@/api/models-ai';
import { apiListObjects, apiListObjectTypes } from '@/api/standards-objects';
import { apiListDepartments } from '@/api/departments';
import { buildDeptTree } from '@/shared/buildDeptTree';
import type { IndustrialObject } from '@/api/types';
import { cmpVer } from '../utils/cmpVer';

export interface UseFlowQueriesParams {
  projectId?: string;
  selectedFlowId: string | null;
  showArchived: boolean;
  deptFilter: string | undefined;
  equipFilter: string | undefined;
  createForm: ReturnType<typeof Form.useForm>[0];
}

export interface UseFlowQueriesResult {
  // 流程列表
  allFlows: FlowSummary[];
  flows: FlowSummary[];
  listLoading: boolean;
  // 选中流程详情
  flow: FlowSummary | undefined;
  runNode: FlowNodeSchema | undefined;
  objCompName: string | undefined;
  // 运行列表
  runs: FlowRunSummary[];
  runsLoading: boolean;
  // 组件列表
  componentOptions: { value: string; label: string; version: string; summary: ComponentSummary }[];
  filteredCompOptions: { value: string; label: string; version: string; summary: ComponentSummary }[];
  compMap: Map<string, ComponentSummary>;
  compOptionsForObj: { value: string; label: string }[];
  compIdToName: Map<string, string>;
  // ingestion tools
  toolTypeDisplayName: Map<string, string>;
  ingestionToolOptions: { value: string; label: string }[];
  // 实验对象
  objMap: Map<string, IndustrialObject>;
  objectOptions: { value: string; label: string; object_type: string }[];
  objectTypeOptions: { value: string; label: string }[];
  // 设备
  equipMap: Map<string, string>;
  equipOptions: { value: string; label: string }[];
  // 部门
  deptMap: Map<string, string>;
  deptOptions: { value: string; label: string }[];
  deptTreeData: ReturnType<typeof buildDeptTree>;
}

export function useFlowQueries(params: UseFlowQueriesParams): UseFlowQueriesResult {
  const { projectId, selectedFlowId, showArchived, deptFilter, equipFilter, createForm } = params;

  // ---- 组件列表查询（P2-C16: 合并为单次查询，消除重复 API 调用）----
  const { data: componentsData } = useQuery({
    queryKey: ['components-for-flow'],
    queryFn: () => apiListComponents(),
  });

  // P2-C20: useMemo 包裹派生计算，避免每次渲染重建
  const componentOptions = useMemo(() => {
    const items = componentsData?.items ?? [];
    const latestByName = new Map<string, ComponentSummary>();
    for (const item of items) {
      const existing = latestByName.get(item.name);
      if (!existing || cmpVer(item.version, existing.version) > 0) {
        latestByName.set(item.name, item);
      }
    }
    return Array.from(latestByName.values())
      .filter((c) => c.status !== 'deprecated')
      .map((c) => ({
        value: c.name,
        label: c.display_name ? `${c.display_name} (${c.name})` : c.name,
        version: c.version,
        summary: c,
      }));
  }, [componentsData]);

  const compMap = useMemo(
    () => new Map<string, ComponentSummary>((componentsData?.items ?? []).map((c) => [c.name, c])),
    [componentsData],
  );

  const compIdToName = useMemo(
    () => new Map<string, string>((componentsData?.items ?? []).map((c) => [c.id, c.name])),
    [componentsData],
  );

  const compOptionsForObj = useMemo(
    () =>
      (componentsData?.items ?? [])
        .filter((c) => c.status !== 'deprecated')
        .map((c) => ({
          value: c.id,
          label: c.display_name || c.name,
        })),
    [componentsData],
  );

  // ---- ingestion tools 查询：构建 tool_type → display_name 映射 ----
  const { data: ingestionToolsData } = useQuery({
    queryKey: ['ingestion-tools-for-flow'],
    queryFn: apiListIngestionTools,
  });
  const toolTypeDisplayName = useMemo(
    () => new Map<string, string>((ingestionToolsData ?? []).map((t) => [t.name, t.display_name])),
    [ingestionToolsData],
  );
  const ingestionToolOptions = useMemo(
    () =>
      (ingestionToolsData ?? []).map((t) => ({
        value: t.name,
        label: t.display_name,
      })),
    [ingestionToolsData],
  );

  // ---- 流程列表查询 ----
  const { data: listData, isLoading: listLoading } = useQuery({
    queryKey: ['flows', projectId],
    queryFn: () => apiListFlows(projectId ? { project_id: projectId } : undefined),
  });

  const allFlows: FlowSummary[] = listData?.items ?? [];
  let flows: FlowSummary[] = showArchived
    ? allFlows.filter((f) => f.status === 'deprecated')
    : allFlows.filter((f) => f.status !== 'deprecated');

  // ---- 实验对象查询 ----
  const { data: objListData } = useQuery({
    queryKey: ['objects-for-flow-list'],
    queryFn: () => apiListObjects({ page_size: 100 }),
  });
  const objMap = useMemo(
    () => new Map<string, IndustrialObject>((objListData?.items ?? []).map((o) => [o.code, o])),
    [objListData],
  );

  // 监听新建任务表单中实验对象的选择值，用于自动填充任务名称
  const watchedExpCodeForName = Form.useWatch('experimental_object_code', createForm);
  useEffect(() => {
    if (watchedExpCodeForName) {
      const obj = objMap.get(watchedExpCodeForName);
      if (obj) {
        const now = new Date();
        const ts = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`;
        createForm.setFieldsValue({ display_name: `${obj.display_name}_${ts}` });
      }
    }
  }, [watchedExpCodeForName, objMap, createForm]);

  // ---- 实验对象类型字典（用于新建任务类型筛选）----
  const { data: objectTypeData } = useQuery({
    queryKey: ['object-types-for-flow-create'],
    queryFn: apiListObjectTypes,
  });
  const objectTypeOptions = useMemo(
    () =>
      (objectTypeData ?? []).map((t) => ({
        value: t.code,
        label: t.display_name,
      })),
    [objectTypeData],
  );
  // 实验对象下拉选项（带 object_type 字段以便按类型过滤）
  const objectOptions = useMemo(
    () =>
      (objListData?.items ?? []).map((o) => ({
        value: o.code,
        label: `${o.display_name} (${o.code})`,
        object_type: o.object_type,
      })),
    [objListData],
  );

  // ---- 设备列表查询 ----
  const { data: equipListData } = useQuery({
    queryKey: ['equipment-for-flow-list'],
    queryFn: () => apiListEquipment({ limit: 100 }),
  });
  const equipMap = useMemo(
    () => new Map<string, string>((equipListData?.items ?? []).map((e) => [e.id, e.display_name])),
    [equipListData],
  );
  const equipOptions = useMemo(
    () =>
      (equipListData?.items ?? []).map((e) => ({
        value: e.id,
        label: e.display_name,
      })),
    [equipListData],
  );

  // ---- 部门列表查询 ----
  const { data: deptListData } = useQuery({
    queryKey: ['departments-for-flow-list'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptMap = useMemo(
    () => new Map<string, string>((deptListData?.items ?? []).map((d) => [d.id, d.display_name])),
    [deptListData],
  );
  const deptOptions = useMemo(
    () =>
      (deptListData?.items ?? []).map((d) => ({
        value: d.id,
        label: d.display_name,
      })),
    [deptListData],
  );
  const deptTreeData = useMemo(
    () => buildDeptTree(deptListData?.items ?? []),
    [deptListData],
  );

  // ---- 选中流程详情查询 ----
  const { data: flow } = useQuery({
    queryKey: ['flow', selectedFlowId],
    queryFn: () => apiGetFlow(selectedFlowId!),
    enabled: !!selectedFlowId,
  });

  // 单节点：取任务的第一个（唯一）节点参数
  const runNode = (flow?.latest_version?.nodes as FlowNodeSchema[] | undefined)?.[0];

  // 从实验对象的 component_id 推导出 component_name（用于执行时预填数据接口）
  const objCompName = flow?.experimental_object_code
    ? (() => {
        const obj = objMap.get(flow.experimental_object_code);
        if (obj?.component_id) return compIdToName.get(obj.component_id) ?? undefined;
        return undefined;
      })()
    : undefined;

  // 接口选项按当前账户可见性过滤（后端已通过 compute_visible_dept_ids 处理，
  // 上下互见 + 同部门可见 + 横向白名单），前端不再按实验对象筛选
  const filteredCompOptions = componentOptions;

  // ---- 选中流程的运行列表查询 ----
  const { data: runsList, isLoading: runsLoading } = useQuery({
    queryKey: ['flow-runs', selectedFlowId],
    queryFn: () => apiListFlowRuns(selectedFlowId!),
    enabled: !!selectedFlowId,
    refetchInterval: (query) => {
      // 有 pending/running 状态的 run 时，每 2 秒轮询
      const items = query.state.data;
      if (items && items.some((r: FlowRunSummary) => r.status === 'pending' || r.status === 'running')) {
        return 2000;
      }
      return false;
    },
  });

  const runs: FlowRunSummary[] = runsList ?? [];

  // 过滤流程列表
  if (deptFilter) {
    flows = flows.filter((f) => f.department_id === deptFilter);
  }
  if (equipFilter) {
    flows = flows.filter((f) => {
      const node = (f.latest_version?.nodes ?? [])[0] as { component_name?: string } | undefined;
      const compName = node?.component_name;
      if (!compName) return false;
      const comp = compMap.get(compName);
      return comp?.equipment_id === equipFilter;
    });
  }

  return {
    allFlows,
    flows,
    listLoading,
    flow,
    runNode,
    objCompName,
    runs,
    runsLoading,
    componentOptions,
    filteredCompOptions,
    compMap,
    compOptionsForObj,
    compIdToName,
    toolTypeDisplayName,
    ingestionToolOptions,
    objMap,
    objectOptions,
    objectTypeOptions,
    equipMap,
    equipOptions,
    deptMap,
    deptOptions,
    deptTreeData,
  };
}
