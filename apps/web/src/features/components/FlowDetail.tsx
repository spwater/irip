import { useState, useRef, useEffect, useMemo } from 'react';
import { useNavigate } from '@tanstack/react-router';
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  TreeSelect,
  Typography,
  message,
} from 'antd';
import { PlusOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCancelFlowRun,
  apiCreateFlow,
  apiCreateFlowRun,
  apiDeleteFlow,
  apiDeleteFlowRun,
  apiArchiveFlow,
  apiRestoreFlow,
  apiGetComponent,
  apiGetFlow,
  apiGetFlowRun,
  apiListComponents,
  apiListEquipment,
  apiListFlows,
  apiListFlowRuns,
  apiPublishFlow,
  apiResumeFlowRun,
  apiUpdateFlow,
  apiPublishComponent,
  type ComponentSummary,
  type FlowNodeSchema,
  type FlowRunSummary,
  type FlowSummary,
} from '@/api/equipment-flows';
import { apiUploadFile, apiListIngestionTools } from '@/api/models-ai';
import { apiListObjects, apiListObjectTypes, apiCreateObject } from '@/api/standards-objects';
import { buildManifestYaml, FORM_FIELD_NAMES, FRESH_FORM_VALUES } from '@/shared/component-utils';
import { ComponentFormFields } from '@/features/components/ComponentFormFields';
import { apiListDepartments } from '@/api/departments';
import { buildDeptTree } from '@/shared/buildDeptTree';
import { extractApiError, type IndustrialObject } from '@/api/types';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { PrivateBadge } from '@/shared/PrivateBadge';
import { DepartmentSelector } from '@/shared/DepartmentSelector';
import { FactModal } from './FactModal';
import {
  fmtTime,
  parseManifest,
  RUN_STATUS_COLOR,
  RUN_STATUS_LABEL,
  STATUS_COLOR,
  STATUS_LABEL,
} from './shared';

const { Text } = Typography;

/**
 * H-16: 批量执行单项结果
 * - succeeded: 执行成功（唯一计为成功的状态）
 * - failed: 执行失败
 * - cancelled: 被取消
 * - timed_out: 轮询耗尽，未在超时内到达终态
 */
interface BatchItemResult {
  fileName: string;
  status: 'succeeded' | 'failed' | 'cancelled' | 'timed_out';
  error?: string;
  runId?: string;
}

/** H-16: 批量轮询单项的最大尝试次数（120 * 2s = 240s = 4min） */
const BATCH_POLL_MAX_ATTEMPTS = 120;
/** H-16: 批量轮询间隔（毫秒） */
const BATCH_POLL_INTERVAL = 500;
/** H-16: 流程运行终态 */
const FLOW_RUN_TERMINAL_STATUSES = ['succeeded', 'failed', 'cancelled'];

export function FlowDetail({
  projectId,
  projectStatus,
}: {
  projectId?: string;
  projectStatus?: string;
} = {}): JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const currentUser = useAuthStore((s) => s.user);
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editFlowId, setEditFlowId] = useState<string | null>(null);
  const [editForm] = Form.useForm();
  const [dataRunId, setDataRunId] = useState<string | null>(null);
  const [factModalOpen, setFactModalOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const artifactMapRef = useRef<Record<string, string>>({});
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [flowPageSize, setFlowPageSize] = useState(10);
  const [runPageSize, setRunPageSize] = useState(10);
  const [createForm] = Form.useForm();
  const [runForm] = Form.useForm();
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [batchFiles, setBatchFiles] = useState<File[]>([]);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchProgress, setBatchProgress] = useState<{ current: number; total: number; status: string } | null>(null);
  /** H-16: 批量执行逐项结果，仅 succeeded 计成功 */
  const [batchResults, setBatchResults] = useState<BatchItemResult[] | null>(null);
  const [selectedType, setSelectedType] = useState<string | undefined>(undefined);
  const [newObjectModalOpen, setNewObjectModalOpen] = useState(false);
  const [newObjectForm] = Form.useForm();
  const [compCreateModalOpen, setCompCreateModalOpen] = useState(false);
  const [compForm] = Form.useForm();
  const [compAdvancedMode, setCompAdvancedMode] = useState(false);
  const [batchSelectedComp, setBatchSelectedComp] = useState<string | undefined>(undefined);
  const [batchOperator, setBatchOperator] = useState<string>('');
  const [batchPrompt, setBatchPrompt] = useState<string>('');

  /** 管理权限检查：所有者 + 上级向下 + 负责人管本部门 */
  const canManage = (flow: FlowSummary | undefined | null): boolean => {
    if (!flow || !currentUser) return false;
    // 平台管理员不受限
    if (currentUser.roles.includes('platform_administrator')) return true;
    // 数据所有者可管理
    if (flow.owner_user_id && currentUser.id === flow.owner_user_id) return true;
    // 实验室负责人可管本部门成员的数据
    if (currentUser.roles.includes('lab_director') && flow.department_id === currentUser.departmentId) return true;
    // 非同部门 → 需要后端判断是否是上级，前端保守返回 false
    return false;
  };

  /** 比较版本号 */
  const cmpVer = (a: string, b: string): number => {
    const pa = a.split('.').map(Number);
    const pb = b.split('.').map(Number);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
      const va = pa[i] ?? 0;
      const vb = pb[i] ?? 0;
      if (va !== vb) return va - vb;
    }
    return 0;
  };

  // ---- 组件列表查询（用于新建流程选择工具）----
  const { data: componentsDataForCreate } = useQuery({
    queryKey: ['components-for-flow-create'],
    queryFn: () => apiListComponents(),
  });
  const componentOptions = (() => {
    const items = componentsDataForCreate?.items ?? [];
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
  })();

  // ---- ingestion tools 查询：构建 tool_type → display_name 映射 ----
  const { data: ingestionToolsData } = useQuery({
    queryKey: ['ingestion-tools-for-flow'],
    queryFn: apiListIngestionTools,
  });
  const toolTypeDisplayName = new Map<string, string>(
    (ingestionToolsData ?? []).map((t) => [t.name, t.display_name]),
  );
  const ingestionToolOptions = (ingestionToolsData ?? []).map((t) => ({
    value: t.name,
    label: t.display_name,
  }));

  // ---- 流程列表查询 ----
  const [showArchived, setShowArchived] = useState(false);
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [equipFilter, setEquipFilter] = useState<string | undefined>(undefined);
  const { data: listData, isLoading: listLoading } = useQuery({
    queryKey: ['flows', projectId],
    queryFn: () => apiListFlows(projectId ? { project_id: projectId } : undefined),
  });

  const allFlows: FlowSummary[] = listData?.items ?? [];
  let flows: FlowSummary[] = showArchived
    ? allFlows.filter((f) => f.status === 'deprecated')
    : allFlows.filter((f) => f.status !== 'deprecated');
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

  // ---- 查询组件列表、实验对象、设备，用于在流程列表展示关联信息 ----
  const { data: compListData } = useQuery({
    queryKey: ['components-for-flow-list'],
    queryFn: () => apiListComponents(),
  });
  // component_name → ComponentSummary（后端已返回当前活跃版本）
  const compMap = new Map<string, ComponentSummary>();
  for (const c of compListData?.items ?? []) {
    compMap.set(c.name, c);
  }

  const { data: objListData } = useQuery({
    queryKey: ['objects-for-flow-list'],
    queryFn: () => apiListObjects({ page_size: 100 }),
  });
  const objMap = new Map<string, IndustrialObject>(
    (objListData?.items ?? []).map((o) => [o.code, o]),
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
  const objectTypeOptions = (objectTypeData ?? []).map((t) => ({
    value: t.code,
    label: t.display_name,
  }));
  // 实验对象下拉选项（带 object_type 字段以便按类型过滤）
  const objectOptions = (objListData?.items ?? []).map((o) => ({
    value: o.code,
    label: `${o.display_name} (${o.code})`,
    object_type: o.object_type,
  }));

  const { data: equipListData } = useQuery({
    queryKey: ['equipment-for-flow-list'],
    queryFn: () => apiListEquipment({ limit: 100 }),
  });
  const equipMap = new Map<string, string>(
    (equipListData?.items ?? []).map((e) => [e.id, e.display_name]),
  );

  const { data: deptListData } = useQuery({
    queryKey: ['departments-for-flow-list'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptMap = new Map<string, string>(
    (deptListData?.items ?? []).map((d) => [d.id, d.display_name]),
  );
  // 部门下拉选项（用于新建任务）
  const deptOptions = (deptListData?.items ?? []).map((d) => ({
    value: d.id,
    label: d.display_name,
  }));
  // 部门树（用于新建实验对象的可见单位）
  const deptTreeData = useMemo(
    () => buildDeptTree(deptListData?.items ?? []),
    [deptListData],
  );

  // ---- 数据接口列表（用于新建实验对象的数据接口选择，按 id 选择）----
  const compOptionsForObj = (componentsDataForCreate?.items ?? [])
    .filter((c) => c.status !== 'deprecated')
    .map((c) => ({
      value: c.id,
      label: c.display_name || c.name,
    }));

  // 从 component_id 到 component_name 的映射（用于执行时从实验对象绑定推导接口）
  const compIdToName = new Map<string, string>(
    (componentsDataForCreate?.items ?? []).map((c) => [c.id, c.name]),
  );
  const equipOptions = (equipListData?.items ?? []).map((e) => ({
    value: e.id,
    label: e.display_name,
  }));

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

  // ---- 创建流程 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateFlow,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      setCreateModalOpen(false);
      createForm.resetFields();
      message.success('流程创建成功');
      setSelectedFlowId(data.id);
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 新建实验对象 Mutation ----
  const createObjectMutation = useMutation({
    mutationFn: apiCreateObject,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['objects-for-flow-list'] });
      setNewObjectModalOpen(false);
      newObjectForm.resetFields();
      message.success('实验对象创建成功');
      // 自动选中新建的实验对象
      createForm.setFieldsValue({ experimental_object_code: data.code });
      editForm.setFieldsValue({ experimental_object_code: data.code });
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 新建数据接口 Mutation ----
  const publishCompMutation = useMutation({
    mutationFn: apiPublishComponent,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      setCompCreateModalOpen(false);
      compForm.resetFields();
      setCompAdvancedMode(false);
      message.success('数据接口创建成功');
      newObjectForm.setFieldsValue({ component_id: data.id });
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 归档 Mutation ----
  const archiveMutation = useMutation({
    mutationFn: apiArchiveFlow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      void queryClient.refetchQueries({ queryKey: ['flows', projectId] });
      setSelectedFlowId(null);
      message.success('流程已归档');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 恢复 Mutation ----
  const restoreMutation = useMutation({
    mutationFn: apiRestoreFlow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['flow', selectedFlowId] });
      message.success('流程已恢复');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 删除流程 Mutation ----
  const deleteFlowMutation = useMutation({
    mutationFn: apiDeleteFlow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      // 若删除的是当前选中流程，清除选中状态
      setSelectedFlowId(null);
      setActiveRunId(null);
      message.success('流程已删除');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 更新流程 Mutation ----
  const updateFlowMutation = useMutation({
    mutationFn: (vars: { flowId: string; displayName: string; departmentId?: string | null; operator?: string | null; projectId?: string | null; experimentalObjectCode?: string | null }) =>
      apiUpdateFlow(vars.flowId, vars.displayName, vars.departmentId, vars.operator, vars.projectId, vars.experimentalObjectCode),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      if (selectedFlowId) {
        void queryClient.invalidateQueries({ queryKey: ['flow', selectedFlowId] });
      }
      setEditModalOpen(false);
      editForm.resetFields();
      message.success('任务名称已更新');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 继续 / 取消 Mutation ----
  const resumeMutation = useMutation({
    mutationFn: apiResumeFlowRun,
    onSuccess: () => {
      void queryClient.refetchQueries({ queryKey: ['flow-runs', selectedFlowId] });
      message.success('已开始执行');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const cancelMutation = useMutation({
    mutationFn: apiCancelFlowRun,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-run', activeRunId] });
      message.success('流程执行已取消');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 删除运行 Mutation ----
  const deleteRunMutation = useMutation({
    mutationFn: apiDeleteFlowRun,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-runs', selectedFlowId] });
      if (activeRunId) {
        setActiveRunId(null);
      }
      message.success('运行记录已删除');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 事件处理 ----
  const handleCreate = async (): Promise<void> => {
    try {
      const values = await createForm.validateFields();
      const expCode = (values.experimental_object_code as string) ?? '';
      createMutation.mutate({
        display_name: values.display_name,
        department_id: (values.department_id as string) ?? null,
        project_id: projectId ?? null,
        operator: (values.operator as string) ?? '',
        experimental_object_code: expCode || null,
      });
    } catch {
      // 校验失败
    }
  };

  // ---- 批量执行 ----
  const handleBatchExecute = async (): Promise<void> => {
    if (!selectedFlowId || batchFiles.length === 0) return;
    if (!batchSelectedComp) {
      message.warning('请先选择数据接口');
      return;
    }
    setBatchRunning(true);
    setBatchProgress({ current: 0, total: batchFiles.length, status: '准备执行...' });

    // 检查是否需要先发布
    const currentNode = (flow?.latest_version?.nodes as FlowNodeSchema[] | undefined)?.[0];
    const needPublish = !currentNode || currentNode.component_name !== batchSelectedComp;

    if (needPublish) {
      const comp = componentOptions.find((c) => c.value === batchSelectedComp);
      if (!comp) {
        message.error('找不到选中的数据接口');
        setBatchRunning(false);
        return;
      }
      let params: Record<string, unknown> = {};
      try {
        const detail = await apiGetComponent(comp.summary.id);
        const parsed = parseManifest(detail.manifest_yaml);
        for (const p of parsed.params) {
          params[p.name] = p.default ?? '';
        }
      } catch {
        // 获取详情失败时用空 params
      }
      const nodes: FlowNodeSchema[] = [{
        node_id: 'n1',
        component_name: batchSelectedComp,
        component_version: comp.version,
        params: {
          ...params,
          experimental_object_code: flow?.experimental_object_code ?? null,
        },
      }];
      try {
        await apiPublishFlow(selectedFlowId, { nodes });
        await queryClient.refetchQueries({ queryKey: ['flow', selectedFlowId] });
      } catch (err) {
        message.error(`发布失败: ${err instanceof Error ? err.message : String(err)}`);
        setBatchRunning(false);
        return;
      }
    }

    // 从 published 版本获取 node 信息（从 queryClient 获取最新数据，确保发布后拿到最新 node）
    const updatedFlow = queryClient.getQueryData<FlowSummary>(['flow', selectedFlowId]);
    const node = (updatedFlow?.latest_version?.nodes as FlowNodeSchema[] | undefined)?.[0];
    const comp = node?.component_name ? compMap.get(node.component_name) : undefined;

    // H-16: 逐项维护 succeeded/failed/cancelled/timed_out
    const results: BatchItemResult[] = [];

    for (let i = 0; i < batchFiles.length; i++) {
      const file = batchFiles[i];
      setBatchProgress({ current: i, total: batchFiles.length, status: `正在上传: ${file.name}` });
      try {
        // 1. 上传文件
        const uploadRes = await apiUploadFile(file);
        // 2. 构建 inputs（prompt 用组件当前活跃版本的值，不用 flow 快照）
        const inputs: Record<string, unknown> = {};
        if (node) {
          for (const key of Object.keys(node.params ?? {})) {
            if (key === 'path') {
              inputs[key] = `artifact:${uploadRes.artifact_id}`;
            } else if (key === 'experimental_object_code') {
              inputs[key] = (node.params as Record<string, unknown>)?.experimental_object_code ?? '';
            } else if (key === 'prompt' && comp?.prompt) {
              inputs[key] = batchPrompt || comp.prompt;
            } else {
              const defaultVal = (node.params as Record<string, unknown>)?.[key];
              inputs[key] = defaultVal ?? '';
            }
          }
        }
        // 执行人存入元信息（为空时取任务执行人）
        inputs['_operator'] = batchOperator || flow?.operator || '';
        inputs['_filename'] = file.name;
        // 3. 创建运行
        setBatchProgress({ current: i, total: batchFiles.length, status: `正在执行: ${file.name}` });
        const run = await apiCreateFlowRun(selectedFlowId, { inputs });
        // 4. 等待执行完成（轮询）— H-16: 轮询耗尽记超时
        let runStatus: string | null = null;
        for (let attempts = 0; attempts < BATCH_POLL_MAX_ATTEMPTS; attempts++) {
          await new Promise((r) => setTimeout(r, BATCH_POLL_INTERVAL));
          const updated = await apiGetFlowRun(run.id);
          runStatus = updated.status;
          if (FLOW_RUN_TERMINAL_STATUSES.includes(updated.status)) {
            break;
          }
        }
        // H-16: 仅 succeeded/failed/cancelled 为终态；轮询耗尽记 timed_out
        if (runStatus && FLOW_RUN_TERMINAL_STATUSES.includes(runStatus)) {
          results.push({
            fileName: file.name,
            status: runStatus as BatchItemResult['status'],
            runId: run.id,
          });
        } else {
          // 轮询耗尽，未到达终态
          results.push({
            fileName: file.name,
            status: 'timed_out',
            runId: run.id,
          });
        }
      } catch (err) {
        // H-16: 记录失败原因
        const errMsg = err instanceof Error ? err.message : String(err);
        results.push({ fileName: file.name, status: 'failed', error: errMsg });
        message.error(`文件 ${file.name} 执行失败: ${errMsg}`);
      }
    }

    // H-16: 准确汇总 — 仅 succeeded 计成功，混合结果用 warning 而非 success
    const summary = results.reduce(
      (acc, r) => {
        acc[r.status]++;
        return acc;
      },
      { succeeded: 0, failed: 0, cancelled: 0, timed_out: 0 } as Record<BatchItemResult['status'], number>,
    );

    setBatchResults(results);
    setBatchProgress({ current: batchFiles.length, total: batchFiles.length, status: '批量执行完成' });
    void queryClient.invalidateQueries({ queryKey: ['flow-runs', selectedFlowId] });
    setBatchRunning(false);
    setBatchFiles([]);

    // H-16: 展示准确汇总 — 有失败/取消/超时时用 warning 而非 success
    const hasIssues = summary.failed > 0 || summary.cancelled > 0 || summary.timed_out > 0;
    if (hasIssues) {
      const parts: string[] = [];
      if (summary.succeeded > 0) parts.push(`${summary.succeeded} 成功`);
      if (summary.failed > 0) parts.push(`${summary.failed} 失败`);
      if (summary.cancelled > 0) parts.push(`${summary.cancelled} 取消`);
      if (summary.timed_out > 0) parts.push(`${summary.timed_out} 超时`);
      message.warning(`批量执行完成: ${parts.join(', ')}`);
    } else {
      message.success(`批量执行完成: ${summary.succeeded} 个文件`);
    }
  };

  // ---- 表格列定义 ----
  const flowColumns: ColumnsType<FlowSummary> = [
    {
      title: '任务名称',
      key: 'name',
      width: 200,
      render: (_: unknown, record: FlowSummary) => (
        <div>
          <Text strong>{record.display_name || record.code}</Text>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {record.code}
            </Text>
          </div>
        </div>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      sorter: (a: FlowSummary, b: FlowSummary) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      defaultSortOrder: 'ascend',
      render: (v: string) => fmtTime(v),
    },
    {
      title: '执行人',
      dataIndex: 'operator',
      key: 'operator',
      width: 100,
      render: (v: string | null) => v ?? <Text type="secondary">-</Text>,
    },
    {
      title: '实验对象',
      dataIndex: 'experimental_object_code',
      key: 'experimental_object_code',
      width: 120,
      render: (code: string | null) => {
        if (!code) return <Text type="secondary">-</Text>;
        const obj = objMap.get(code);
        return (
          <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
            {obj?.display_name ?? code}
          </Tag>
        );
      },
    },
    {
      title: '任务来源',
      key: 'department',
      width: 300,
      render: (_: unknown, record: FlowSummary) => {
        const deptName = record.department_id ? deptMap.get(record.department_id) : null;
        if (!deptName) return <Text type="secondary">-</Text>;
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {deptName && (
              <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                {deptName}
              </Tag>
            )}
            {(record as Record<string, unknown>).visibility_scope === 'private' && (
              <PrivateBadge visibility_scope="private" />
            )}
          </div>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] ?? 'default'}>{STATUS_LABEL[v] ?? v}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_: unknown, record: FlowSummary) =>
        record.status === 'deprecated' ? (
          <Space size="small">
            <Popconfirm
              title="确定恢复该流程？"
              onConfirm={(e) => {
                e?.stopPropagation();
                restoreMutation.mutate(record.id);
              }}
              okText="恢复"
              cancelText="取消"
            >
              <Button
                type="link"
                size="small"
                onClick={(e) => e.stopPropagation()}
                loading={restoreMutation.isPending}
              >
                恢复
              </Button>
            </Popconfirm>
            <Popconfirm
              title="确定删除该流程？"
              description="将同时删除其所有版本和运行记录，不可撤销"
              onConfirm={(e) => {
                e?.stopPropagation();
                deleteFlowMutation.mutate(record.id);
              }}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              disabled={!canManage(record)}
            >
              <Button
                type="link"
                size="small"
                danger
                onClick={(e) => e.stopPropagation()}
                loading={deleteFlowMutation.isPending}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <Space size="small">
            <Button
              type="link"
              size="small"
              disabled={!canManage(record)}
              onClick={(e) => {
                e.stopPropagation();
                setEditFlowId(record.id);
                editForm.setFieldsValue({
                  display_name: record.display_name,
                  code: record.code,
                  department_id: record.department_id ?? undefined,
                  operator: record.operator ?? undefined,
                  experimental_object_code: record.experimental_object_code ?? undefined,
                });
                setEditModalOpen(true);
              }}
            >
              编辑
            </Button>
            {(record as Record<string, unknown>).visibility_scope === 'private' && (
              <Popconfirm
                title="确认公开此流程？"
                description="此操作【不可逆】，公开后部门内所有成员可见。"
                onConfirm={async (e) => {
                  e?.stopPropagation();
                  try {
                    await import('@/api/client').then(({ http }) =>
                      http.patch(`/flows/${record.id}`, { visibility_scope: 'tree' })
                    );
                    message.success('流程已公开');
                    void queryClient.invalidateQueries({ queryKey: ['flows'] });
                  } catch (err) {
                    message.error(extractApiError(err));
                  }
                }}
                okText="确认公开"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button
                  type="link"
                  size="small"
                  danger
                  onClick={(e) => e.stopPropagation()}
                >
                  公开
                </Button>
              </Popconfirm>
            )}
            <Popconfirm
              title="确定归档该流程？"
              onConfirm={(e) => {
                e?.stopPropagation();
                archiveMutation.mutate(record.id);
              }}
              okText="归档"
              cancelText="取消"
              disabled={!canManage(record)}
            >
              <Button
                type="link"
                size="small"
                danger
                onClick={(e) => e.stopPropagation()}
                loading={archiveMutation.isPending}
              >
                归档
              </Button>
            </Popconfirm>
          </Space>
        ),
    },
  ];


  const canExecute = !!selectedFlowId;

  return (
    <div>
      <Space style={{ marginBottom: 16, alignItems: 'center' }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateModalOpen(true)}
          disabled={projectStatus === 'archived'}
        >
          新建任务
        </Button>
        <Button
          type={showArchived ? 'default' : 'primary'}
          onClick={() => setShowArchived(false)}
        >
          活跃
        </Button>
        <Button
          type={showArchived ? 'primary' : 'default'}
          onClick={() => setShowArchived(true)}
        >
          归档
        </Button>
        <Select
          placeholder="所属单位筛选"
          style={{ width: 200 }}
          value={deptFilter ?? '__all__'}
          onChange={(val: string) => setDeptFilter(val === '__all__' ? undefined : val)}
          options={[{ value: '__all__', label: '全部' }, ...deptOptions]}
        />
        <Select
          placeholder="实验设备筛选"
          style={{ width: 200 }}
          value={equipFilter ?? '__all__'}
          onChange={(val: string) => setEquipFilter(val === '__all__' ? undefined : val)}
          options={[{ value: '__all__', label: '全部' }, ...equipOptions]}
        />
      </Space>

      {/* 任务列表 */}
      <Card title="任务列表" style={{ marginBottom: 16 }}>
        <Table<FlowSummary>
          columns={flowColumns}
          dataSource={flows}
          rowKey="id"
          loading={listLoading}
          pagination={{
            pageSize: flowPageSize,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50],
            onShowSizeChange: (_: number, size: number) => setFlowPageSize(size),
          }}
          size="middle"
          onRow={(record) => ({
            onClick: () => setSelectedFlowId(record.id),
            style: { cursor: 'pointer' },
          })}
        />
      </Card>

      {/* 运行管理 */}
      {selectedFlowId && (
        <Card
          title={
            <Space>
              <span>数据管理</span>
              <Button
                type="primary"
                size="small"
                disabled={!canExecute}
                icon={<PlayCircleOutlined />}
                onClick={() => {
                  setBatchFiles([]);
                  setBatchProgress(null);
                  setBatchSelectedComp(objCompName ?? runNode?.component_name ?? undefined);
                  setBatchOperator(flow?.operator ?? '');
                  setBatchPrompt('');
                  setBatchResults(null);
                  setBatchModalOpen(true);
                }}
              >
                提取
              </Button>
            </Space>
          }
          style={{ marginBottom: 16 }}
        >
          {/* 运行列表 */}
          <Table<FlowRunSummary>
            columns={[
              { title: '数据名称', key: 'data_name', width: 280, ellipsis: true,
                render: (_: unknown, record: FlowRunSummary) => {
                  const taskName = flow?.display_name ?? '';
                  const fileName = record.source_filename ?? '';
                  const displayName = fileName ? `${taskName}-${fileName}` : (taskName || record.id.slice(0, 8));
                  const out = record.output_summary;
                  const meta = (out?._metadata ?? {}) as Record<string, unknown>;
                  const header = (meta.header ?? meta.metadata ?? {}) as Record<string, unknown>;
                  const points = (meta.points ?? []) as { name: string; value: unknown; unit: string | null }[];
                  const seriesList = (meta.series ?? []) as { name: string; columns: string[]; rows: unknown[][] }[];
                  const parts: string[] = [];
                  if (Object.keys(header).length > 0) {
                    parts.push('=== 标头 ===');
                    parts.push(JSON.stringify(header, null, 2));
                  }
                  if (points.length > 0) {
                    parts.push('=== 指标（前 3 个） ===');
                    parts.push(JSON.stringify(points.slice(0, 3), null, 2));
                  }
                  if (seriesList.length > 0) {
                    parts.push(`=== 序列（${seriesList.length} 组） ===`);
                    for (const s of seriesList.slice(0, 2)) {
                      parts.push(`${s.name}: ${s.rows?.length ?? 0} 行`);
                    }
                  }
                  const previewText = parts.join('\n');
                  const clickable = record.persisted_as_fact && record.fact_id;
                  return (
                    <Tooltip
                      title={previewText ? (
                        <pre style={{ fontSize: 11, maxHeight: 300, overflow: 'auto', margin: 0 }}>
                          {previewText}
                        </pre>
                      ) : '暂无输出数据'}
                      placement="rightTop"
                      overlayStyle={{ maxWidth: 500 }}
                    >
                      <Text
                        style={{
                          fontFamily: 'monospace', fontSize: 12,
                          cursor: clickable ? 'pointer' : 'default',
                          color: clickable ? 'var(--ocean-action-primary)' : 'inherit',
                        }}
                        onClick={(e: React.MouseEvent) => {
                          if (clickable && record.fact_id) {
                            e.stopPropagation();
                            void navigate({
                              to: '/facts/$factId',
                              params: { factId: record.fact_id },
                              search: projectId ? { project: projectId } : undefined,
                            });
                          }
                        }}
                      >
                        {displayName}
                      </Text>
                    </Tooltip>
                  );
                },
              },
              { title: '数据来源', key: 'component', width: 280,
                render: () => {
                  const node = (flow?.latest_version?.nodes ?? [])[0] as { component_name?: string } | undefined;
                  if (!node?.component_name) return <Text type="secondary">-</Text>;
                  const comp = compMap.get(node.component_name);
                  const eqName = comp?.equipment_id ? equipMap.get(comp.equipment_id) : null;
                  return (
                    <Space size={4}>
                      <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                        {comp?.display_name ?? node.component_name}
                      </Tag>
                      {eqName && (
                        <>
                          <Text type="secondary" style={{ fontSize: 12 }}>→</Text>
                          <Tag color="cyan" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                            {eqName}
                          </Tag>
                        </>
                      )}
                    </Space>
                  );
                },
              },
              { title: '状态', dataIndex: 'status', key: 'status', width: 100,
                render: (s: string, record: FlowRunSummary) =>
                  s === 'failed' && record.error_message
                    ? <Tooltip title={record.error_message}><Tag color={RUN_STATUS_COLOR[s] ?? 'default'}>{RUN_STATUS_LABEL[s] ?? s}</Tag></Tooltip>
                    : <Tag color={RUN_STATUS_COLOR[s] ?? 'default'}>{RUN_STATUS_LABEL[s] ?? s}</Tag> },
              { title: '执行人', dataIndex: 'operator', key: 'operator', width: 100,
                render: (v: string | null) => v ?? <Text type="secondary">-</Text> },
              { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180,
                render: (v: string) => fmtTime(v) },
              { title: '耗时', key: 'duration', width: 100,
                render: (_: unknown, record: FlowRunSummary) => {
                  if (!record.created_at || !record.completed_at) return '-';
                  const ms = new Date(record.completed_at).getTime() - new Date(record.created_at).getTime();
                  if (ms < 1000) return `${ms}ms`;
                  return `${(ms / 1000).toFixed(1)}s`;
                },
              },
              { title: '已存', key: 'persisted', width: 60, align: 'center' as const,
                render: (_: unknown, record: FlowRunSummary) =>
                  record.persisted_as_fact
                    ? <span style={{ color: 'var(--ocean-status-success)', fontWeight: 'bold', fontSize: 16 }}>&#10003;</span>
                    : null,
              },
              { title: '操作', key: 'action', width: 200,
                render: (_: unknown, record: FlowRunSummary) => (
                  <Space size="small">
                    {record.status === 'succeeded' && (
                      <Button type="link" size="small"
                        disabled={!canManage(flow)}
                        onClick={() => {
                          if (!canManage(flow)) return;
                          setDataRunId(record.id);
                          setFactModalOpen(true);
                        }}
                      >
                        数据入库
                      </Button>
                    )}
                    {record.status === 'failed' && (
                      <Popconfirm title="确认重试？" onConfirm={() => resumeMutation.mutate(record.id)} okText="确定" cancelText="取消">
                        <Button type="link" size="small">继续</Button>
                      </Popconfirm>
                    )}
                    {activeRunId === record.id && record.status !== 'pending' && record.status !== 'succeeded' && record.status !== 'cancelled' && record.status !== 'failed' && (
                      <>
                        <Popconfirm title="确认继续？" onConfirm={() => resumeMutation.mutate(record.id)} okText="确定" cancelText="取消">
                          <Button type="link" size="small">继续</Button>
                        </Popconfirm>
                        <Popconfirm title="确认取消？" onConfirm={() => cancelMutation.mutate(record.id)} okText="确定" cancelText="取消">
                          <Button type="link" size="small" danger>取消</Button>
                        </Popconfirm>
                      </>
                    )}
                    <Popconfirm
                      title="确定删除该运行记录？"
                      description="将同时删除其所有节点执行记录，不可撤销"
                      onConfirm={() => deleteRunMutation.mutate(record.id)}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                    >
                      <Button type="link" size="small" danger loading={deleteRunMutation.isPending}>
                        删除
                      </Button>
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
            dataSource={runs}
            rowKey="id"
            loading={runsLoading}
            pagination={{
              pageSize: runPageSize,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50],
              onShowSizeChange: (_: number, size: number) => setRunPageSize(size),
            }}
            size="small"
            style={{ marginBottom: 16 }}
          />

          {/* 数据入库 Modal */}
          <FactModal
            runId={dataRunId}
            flow={flow}
            deptMap={deptMap}
            compMap={compMap}
            open={factModalOpen}
            onClose={() => setFactModalOpen(false)}
          />
        </Card>
      )}

      {/* 新建任务 Modal */}
      <Modal
        title="新建任务"
        open={createModalOpen}
        onOk={handleCreate}
        onCancel={() => {
          setCreateModalOpen(false);
          createForm.resetFields();
          setSelectedType(undefined);
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createForm} layout="vertical">
          <Row gutter={16}>
            <Col span={6}>
              <Form.Item label="类型">
                <Select
                  placeholder="全部"
                  allowClear
                  value={selectedType}
                  onChange={(val: string | undefined) => {
                    setSelectedType(val);
                    // 如果当前选中的实验对象类型不匹配，清空
                    const currentObj = createForm.getFieldValue('experimental_object_code');
                    if (currentObj) {
                      const obj = objMap.get(currentObj);
                      if (obj && val && obj.object_type !== val) {
                        createForm.setFieldsValue({ experimental_object_code: undefined });
                      }
                    }
                  }}
                  options={objectTypeOptions}
                />
              </Form.Item>
            </Col>
            <Col span={18}>
              <Form.Item label="实验对象">
                <Space.Compact style={{ width: '100%' }}>
                  <Form.Item name="experimental_object_code" noStyle>
                    <Select
                      placeholder="请选择实验对象"
                      allowClear
                      showSearch
                      optionFilterProp="label"
                      options={objectOptions.filter((o) => !selectedType || o.object_type === selectedType)}
                      style={{ width: 'calc(100% - 40px)' }}
                    />
                  </Form.Item>
                  <Button
                    icon={<PlusOutlined />}
                    onClick={() => {
                      newObjectForm.resetFields();
                      if (selectedType) {
                        newObjectForm.setFieldsValue({ object_type: selectedType });
                      }
                      setNewObjectModalOpen(true);
                    }}
                    title="新建实验对象"
                    style={{ width: 40 }}
                  />
                </Space.Compact>
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="department_id"
            label="所属单位"
            rules={[{ required: true, message: '请选择所属单位' }]}
          >
            <DepartmentSelector
              placeholder="请选择所属单位"
              allowRoot={true}
            />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="任务名称"
            rules={[{ required: true, message: '请输入任务名称' }]}
          >
            <Input placeholder="如：篦冷机分析任务" maxLength={200} />
          </Form.Item>
          <Form.Item
            name="operator"
            label="执行人"
            rules={[{ required: true, message: '请输入执行人' }]}
          >
            <Input placeholder="如：宋昊" maxLength={100} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑任务 Modal */}
      <Modal
        title="编辑任务"
        open={editModalOpen}
        onOk={async () => {
          try {
            const values = await editForm.validateFields(['display_name', 'department_id', 'operator', 'experimental_object_code']);
            if (editFlowId) {
              updateFlowMutation.mutate(
                {
                  flowId: editFlowId,
                  displayName: values.display_name as string,
                  departmentId: (values.department_id as string) ?? null,
                  operator: (values.operator as string) ?? null,
                  projectId: projectId ?? null,
                  experimentalObjectCode: (values.experimental_object_code as string) ?? null,
                },
              );
            }
          } catch {
            // 校验失败
          }
        }}
        onCancel={() => {
          setEditModalOpen(false);
          editForm.resetFields();
        }}
        confirmLoading={updateFlowMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="display_name"
            label="任务名称"
            rules={[{ required: true, message: '请输入任务名称' }]}
          >
            <Input placeholder="请输入任务名称" maxLength={200} />
          </Form.Item>
          <Form.Item name="department_id" label="所属单位">
            <DepartmentSelector
              placeholder="请选择所属单位"
              allowRoot={true}
            />
          </Form.Item>
          <Form.Item
            name="operator"
            label="执行人"
            rules={[{ required: true, message: '请输入执行人' }]}
          >
            <Input placeholder="如：宋昊" maxLength={100} />
          </Form.Item>
          <Form.Item label="实验对象">
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="experimental_object_code" noStyle>
                <Select
                  placeholder="请选择实验对象"
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  options={objectOptions}
                  style={{ width: 'calc(100% - 40px)' }}
                />
              </Form.Item>
              <Button
                icon={<PlusOutlined />}
                onClick={() => {
                  newObjectForm.resetFields();
                  setNewObjectModalOpen(true);
                }}
                title="新建实验对象"
                style={{ width: 40 }}
              />
            </Space.Compact>
          </Form.Item>
        </Form>
      </Modal>

      {/* 新建实验对象 Modal */}
      <Modal
        title="新建实验对象"
        open={newObjectModalOpen}
        onCancel={() => {
          setNewObjectModalOpen(false);
          newObjectForm.resetFields();
        }}
        footer={
          <Space>
            <Button onClick={() => {
              setNewObjectModalOpen(false);
              newObjectForm.resetFields();
            }}>
              取消
            </Button>
            <Button
              type="primary"
              onClick={async () => {
                try {
                  const values = await newObjectForm.validateFields();
                  createObjectMutation.mutate({
                    display_name: values.display_name as string,
                    object_type: (values.object_type as string) ?? 'unknown',
                    description: (values.description as string) ?? undefined,
                    department_id: (values.department_id as string) ?? undefined,
                    visible_departments: (values.visible_departments as string[]) ?? undefined,
                    component_id: (values.component_id as string) ?? undefined,
                  });
                } catch {
                  // validation error
                }
              }}
              loading={createObjectMutation.isPending}
            >
              创建
            </Button>
          </Space>
        }
        width={600}
        destroyOnClose
      >
        <Form form={newObjectForm} layout="vertical" initialValues={{ department_id: currentUser?.departmentId }}>
          <Form.Item
            name="display_name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="如：铝合金" maxLength={200} />
          </Form.Item>
          <Form.Item
            name="object_type"
            label="类型"
            rules={[{ required: true, message: '请选择类型' }]}
          >
            <Select
              placeholder="选择实验对象类型"
              options={objectTypeOptions}
            />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea
              placeholder="对象描述（可选）"
              rows={3}
              maxLength={2000}
            />
          </Form.Item>
          <Form.Item name="department_id" label="所属单位" rules={[{ required: true, message: '请选择所属单位' }]}>
            <Select
              placeholder="选择所属单位"
              showSearch
              optionFilterProp="label"
              options={deptOptions}
            />
          </Form.Item>
          <Form.Item
            name="visible_departments"
            label="可见单位"
            tooltip="选填。默认按部门层级可见（上级可看下级、下级可看上级）。如需对其他部门可见，请在此添加。"
          >
            <TreeSelect
              treeData={deptTreeData}
              treeCheckable
              treeDefaultExpandAll
              showSearch
              treeNodeFilterProp="title"
              placeholder="不选则按部门层级默认可见"
              allowClear
              style={{ width: '100%' }}
              maxTagCount={5}
            />
          </Form.Item>
          <Form.Item label="数据接口">
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="component_id" noStyle>
                <Select
                  placeholder="选择数据接口（可选）"
                  showSearch
                  optionFilterProp="label"
                  options={compOptionsForObj}
                  allowClear
                  style={{ width: 'calc(100% - 40px)' }}
                />
              </Form.Item>
              <Button
                icon={<PlusOutlined />}
                onClick={() => {
                  const curDept = newObjectForm.getFieldValue('department_id') as string | undefined;
                  const curName = newObjectForm.getFieldValue('display_name') as string | undefined;
                  compForm.resetFields();
                  setCompAdvancedMode(false);
                  compForm.setFieldsValue({
                    ...FRESH_FORM_VALUES,
                    tool_type: 'llm_converter',
                    department_id: curDept,
                    display_name: curName ? `${curName}接口` : undefined,
                  });
                  setCompCreateModalOpen(true);
                }}
                title="新建数据接口"
                style={{ width: 40 }}
              />
            </Space.Compact>
          </Form.Item>
        </Form>
      </Modal>

      {/* 新建数据接口弹窗（完整表单，复用 ComponentFormFields） */}
      <Modal
        title="新建数据接口"
        open={compCreateModalOpen}
        onOk={async () => {
          try {
            if (compAdvancedMode) {
              const values = await compForm.validateFields(['manifest_yaml', 'department_id', 'visible_departments']);
              publishCompMutation.mutate({
                manifest_yaml: values.manifest_yaml as string,
                department_id: (values.department_id as string) ?? null,
                visible_departments: (values.visible_departments as string[] | undefined) ?? null,
              });
            } else {
              const values = await compForm.validateFields([...FORM_FIELD_NAMES, 'department_id', 'visible_departments']);
              const yaml = buildManifestYaml({
                display_name: values.display_name as string,
                description: (values.description as string) ?? '',
                prompt: (values.prompt as string) ?? '',
                tool_type: (values.tool_type as string) ?? 'llm_converter',
              });
              publishCompMutation.mutate({
                manifest_yaml: yaml,
                department_id: (values.department_id as string) ?? null,
                visible_departments: (values.visible_departments as string[] | undefined) ?? null,
              });
            }
          } catch {
            // validation error
          }
        }}
        onCancel={() => {
          setCompCreateModalOpen(false);
          compForm.resetFields();
          setCompAdvancedMode(false);
        }}
        confirmLoading={publishCompMutation.isPending}
        okText="发布"
        cancelText="取消"
        width={680}
        destroyOnClose
      >
        <Form form={compForm} layout="vertical">
          <div style={{ marginBottom: 16 }}>
            <Space align="center">
              <Text>高级模式</Text>
              <Switch
                checked={compAdvancedMode}
                onChange={(checked) => {
                  if (checked) {
                    const vals = compForm.getFieldsValue();
                    const yaml = buildManifestYaml({
                      display_name: vals.display_name ?? '',
                      description: vals.description ?? '',
                      prompt: vals.prompt ?? '',
                      tool_type: vals.tool_type ?? 'llm_converter',
                    });
                    compForm.setFieldsValue({ manifest_yaml: yaml });
                  }
                  setCompAdvancedMode(checked);
                }}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                {compAdvancedMode ? '直接编辑 YAML 全文' : '填写表单字段，自动生成 YAML'}
              </Text>
            </Space>
          </div>
          {compAdvancedMode ? (
            <Form.Item
              name="manifest_yaml"
              label="接口清单 (YAML)"
              rules={[{ required: true, message: '请输入 YAML' }, { min: 10, message: '清单内容过短' }]}
            >
              <Input.TextArea
                placeholder={'name: iface_ffffffff  # 自动生成\nkind: ingestion\ndisplay_name: "接口名"\n...'}
                rows={16}
                style={{ fontFamily: 'monospace', fontSize: 13 }}
              />
            </Form.Item>
          ) : (
            <ComponentFormFields ingestionToolOptions={ingestionToolOptions} />
          )}
          <Form.Item name="department_id" label="所属单位">
            <Select
              placeholder="选择所属单位（可选）"
              allowClear
              showSearch
              optionFilterProp="label"
              options={deptOptions}
            />
          </Form.Item>
          <Form.Item
            name="visible_departments"
            label="可见单位"
            tooltip="选填。默认按部门层级可见。如需对其他部门可见，请在此添加。"
          >
            <TreeSelect
              treeData={deptTreeData}
              treeCheckable
              treeDefaultExpandAll
              showSearch
              treeNodeFilterProp="title"
              placeholder="不选则按部门层级默认可见"
              allowClear
              style={{ width: '100%' }}
              maxTagCount={5}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 执行 Modal（原批处理，单文件=单次执行，多文件=批处理） */}

      {/* 批处理 Modal */}
      <Modal
        title="提取"
        open={batchModalOpen}
        onCancel={() => {
          if (!batchRunning) {
            setBatchModalOpen(false);
            setBatchFiles([]);
            setBatchProgress(null);
            setBatchSelectedComp(undefined);
            setBatchOperator('');
            setBatchResults(null);
          }
        }}
        footer={
          batchRunning ? null : batchResults ? (
            <Button type="primary" onClick={() => {
              setBatchModalOpen(false);
              setBatchFiles([]);
              setBatchProgress(null);
              setBatchResults(null);
              setBatchSelectedComp(undefined);
              setBatchOperator('');
            }}>
              关闭
            </Button>
          ) : (
            <Space>
              <Button onClick={() => setBatchModalOpen(false)}>取消</Button>
              <Button
                type="primary"
                disabled={batchFiles.length === 0}
                onClick={() => void handleBatchExecute()}
              >
                开始执行 ({batchFiles.length} 个文件)
              </Button>
            </Space>
          )
        }
        width={600}
      >
        {batchRunning && batchProgress ? (
          <div style={{ textAlign: 'center', padding: 24 }}>
            <Spin size="large" />
            <div style={{ marginTop: 16 }}>
              <Text strong>
                进度: {batchProgress.current} / {batchProgress.total}
              </Text>
            </div>
            <div style={{ marginTop: 8 }}>
              <Text type="secondary">{batchProgress.status}</Text>
            </div>
          </div>
        ) : batchResults ? (
          /* H-16: 展示批量执行结果，含失败原因与状态汇总 */
          <div>
            {(() => {
              const summary = batchResults.reduce(
                (acc, r) => { acc[r.status]++; return acc; },
                { succeeded: 0, failed: 0, cancelled: 0, timed_out: 0 } as Record<BatchItemResult['status'], number>,
              );
              const hasIssues = summary.failed > 0 || summary.cancelled > 0 || summary.timed_out > 0;
              return (
                <Alert
                  type={hasIssues ? 'warning' : 'success'}
                  message={
                    hasIssues
                      ? `批量执行完成: ${summary.succeeded} 成功, ${summary.failed} 失败, ${summary.cancelled} 取消, ${summary.timed_out} 超时`
                      : `批量执行完成: ${summary.succeeded} 个文件全部成功`
                  }
                  style={{ marginBottom: 16 }}
                />
              );
            })()}
            {/* H-16: 展示失败原因与可重试状态 */}
            {batchResults
              .filter((r) => r.status === 'failed' || r.status === 'timed_out')
              .map((r, idx) => (
                <Alert
                  key={idx}
                  type="error"
                  message={`${r.fileName}: ${r.status === 'timed_out' ? '执行超时' : '执行失败'}`}
                  description={r.error || (r.status === 'timed_out' ? '轮询超时，未在规定时间内到达终态' : '未知原因')}
                  style={{ marginBottom: 8 }}
                />
              ))}
          </div>
        ) : (
          <>
            {batchSelectedComp && (() => {
              const runComp = batchSelectedComp ? compMap.get(batchSelectedComp) : undefined;
              const compOpt = componentOptions.find((c) => c.value === batchSelectedComp);
              const eqName = runComp?.equipment_id ? equipMap.get(runComp.equipment_id) : null;
              const converterName = runComp?.tool_type ? toolTypeDisplayName.get(runComp.tool_type) : null;
              return (runComp || compOpt) ? (
                <div style={{ marginBottom: 16, padding: '8px 12px', background: 'var(--ocean-surface-structural)', borderRadius: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Space size={6}>
                    <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                      {runComp?.display_name ?? compOpt?.label ?? batchSelectedComp}
                    </Tag>
                    {eqName && (
                      <>
                        <Text type="secondary" style={{ fontSize: 12 }}>→</Text>
                        <Tag color="cyan" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                          {eqName}
                        </Tag>
                      </>
                    )}
                  </Space>
                  {converterName && (
                    <Tag color="orange" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                      {converterName}
                    </Tag>
                  )}
                </div>
              ) : null;
            })()}
            <Form layout="vertical">
            <Form.Item label="数据来源" required>
              <Select
                placeholder="请选择数据接口"
                showSearch
                optionFilterProp="label"
                options={filteredCompOptions}
                value={batchSelectedComp}
                onChange={(value: string) => setBatchSelectedComp(value)}
              />
            </Form.Item>
            <Form.Item label="执行人">
              <Input
                value={batchOperator}
                onChange={(e) => setBatchOperator(e.target.value)}
                placeholder="执行人"
                maxLength={100}
              />
            </Form.Item>
            <input
              type="file"
              multiple
              id="batch-file-input"
              style={{ display: 'none' }}
              onChange={(e) => {
                const files = Array.from(e.target.files ?? []);
                setBatchFiles(files);
                if (e.target) e.target.value = '';
              }}
            />
            <div
              onClick={() => document.getElementById('batch-file-input')?.click()}
              style={{
                border: '2px dashed var(--ocean-border-strong)',
                borderRadius: 8,
                padding: 32,
                textAlign: 'center',
                cursor: 'pointer',
                marginBottom: 16,
              }}
            >
              <Text type="secondary" style={{ fontSize: 14 }}>
                {batchFiles.length > 0
                  ? `已选择 ${batchFiles.length} 个文件`
                  : '点击选择多个文件'}
              </Text>
              {batchFiles.length > 0 && (
                <div style={{ marginTop: 8, textAlign: 'left', maxHeight: 200, overflow: 'auto' }}>
                  {batchFiles.map((f, i) => (
                    <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--ocean-border-subtle)' }}>
                      <Text style={{ fontSize: 13 }}>{f.name}</Text>
                      <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                        ({(f.size / 1024).toFixed(0)} KB)
                      </Text>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {batchSelectedComp && (() => {
              const batchComp = batchSelectedComp ? compMap.get(batchSelectedComp) : undefined;
              if (batchComp?.tool_type !== 'llm_converter') return null;
              return (
                <Form.Item label="大模型提示词">
                  <Input.TextArea
                    value={batchPrompt || batchComp?.prompt || ''}
                    onChange={(e) => setBatchPrompt(e.target.value)}
                    rows={6}
                    placeholder="大模型提示词"
                  />
                </Form.Item>
              );
            })()}
            <Text type="secondary" style={{ fontSize: 12 }}>
              将使用当前任务的数据接口，逐个上传文件并执行。文件合规性由用户自行负责。
            </Text>
            </Form>
          </>
        )}
      </Modal>

      {/* 隐藏的文件上传 input（供执行弹窗的 path 字段使用） */}
      <input
        ref={fileInputRef}
        type="file"
        style={{ display: 'none' }}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (!file) return;
          const formKey = fileInputRef.current?.dataset.formkey;
          if (!formKey) return;
          try {
            const res = await apiUploadFile(file);
            runForm.setFieldValue(formKey, file.name);
            artifactMapRef.current[formKey] = `artifact:${res.artifact_id}`;
            message.success(`文件已上传: ${file.name}`);
          } catch (err) {
            message.error(`上传失败: ${err instanceof Error ? err.message : String(err)}`);
          } finally {
            if (fileInputRef.current) {
              fileInputRef.current.value = '';
              delete fileInputRef.current.dataset.formkey;
            }
          }
        }}
      />
    </div>
  );
}

export default FlowDetail;
