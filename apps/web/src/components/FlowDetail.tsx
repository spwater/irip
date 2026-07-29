import { useState, useRef } from 'react';
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
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
  type ComponentSummary,
  type FlowNodeSchema,
  type FlowRunSummary,
  type FlowSummary,
} from '@/api/equipment-flows';
import { apiUploadFile } from '@/api/models-ai';
import { apiListObjects } from '@/api/standards-objects';
import { apiListDepartments } from '@/api/departments';
import { extractApiError, type IndustrialObject } from '@/api/types';
import { FactModal } from './flow/FactModal';
import {
  fmtTime,
  parseManifest,
  RUN_STATUS_COLOR,
  RUN_STATUS_LABEL,
  STATUS_COLOR,
  STATUS_LABEL,
} from './flow/shared';

const { Text } = Typography;

export function FlowDetail(): JSX.Element {
  const queryClient = useQueryClient();
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editFlowId, setEditFlowId] = useState<string | null>(null);
  const [editForm] = Form.useForm();
  const [dataRunId, setDataRunId] = useState<string | null>(null);
  const [factModalOpen, setFactModalOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadLoading, setUploadLoading] = useState<string | null>(null);
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
      .filter((c) => c.status !== 'deprecated' && c.experimental_object_code)
      .map((c) => ({
        value: c.name,
        label: c.display_name ? `${c.display_name} (${c.name})` : c.name,
        version: c.version,
        summary: c,
      }));
  })();

  // ---- 流程列表查询 ----
  const [showArchived, setShowArchived] = useState(false);
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [equipFilter, setEquipFilter] = useState<string | undefined>(undefined);
  const { data: listData, isLoading: listLoading } = useQuery({
    queryKey: ['flows'],
    queryFn: () => apiListFlows(),
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
  const runParamEntries = (() => {
    if (!runNode) return [];
    const params = (runNode.params as Record<string, unknown>) ?? {};
    const entries = Object.entries(params).filter(([key]) => key !== 'experimental_object_code');
    const orderedKeys = ['path', 'file_engine', 'prompt'];
    return entries.sort((a, b) => {
      const ai = orderedKeys.indexOf(a[0]);
      const bi = orderedKeys.indexOf(b[0]);
      if (ai >= 0 && bi >= 0) return ai - bi;
      if (ai >= 0) return -1;
      if (bi >= 0) return 1;
      return 0;
    });
  })();

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
      void queryClient.invalidateQueries({ queryKey: ['flows'] });
      setCreateModalOpen(false);
      createForm.resetFields();
      message.success('流程创建成功');
      setSelectedFlowId(data.id);
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 发布流程 Mutation ----
  const publishMutation = useMutation({
    mutationFn: (vars: { flowId: string; body: Parameters<typeof apiPublishFlow>[1] }) =>
      apiPublishFlow(vars.flowId, vars.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows'] });
      void queryClient.invalidateQueries({ queryKey: ['flow', selectedFlowId] });
      message.success('流程发布成功');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 归档 Mutation ----
  const archiveMutation = useMutation({
    mutationFn: apiArchiveFlow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows'] });
      void queryClient.refetchQueries({ queryKey: ['flows'] });
      setSelectedFlowId(null);
      message.success('流程已归档');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 恢复 Mutation ----
  const restoreMutation = useMutation({
    mutationFn: apiRestoreFlow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows'] });
      void queryClient.invalidateQueries({ queryKey: ['flow', selectedFlowId] });
      message.success('流程已恢复');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 删除流程 Mutation ----
  const deleteFlowMutation = useMutation({
    mutationFn: apiDeleteFlow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows'] });
      // 若删除的是当前选中流程，清除选中状态
      setSelectedFlowId(null);
      setActiveRunId(null);
      message.success('流程已删除');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 更新流程 Mutation ----
  const updateFlowMutation = useMutation({
    mutationFn: (vars: { flowId: string; displayName: string; departmentId?: string | null; projectName?: string | null; operator?: string | null }) =>
      apiUpdateFlow(vars.flowId, vars.displayName, vars.departmentId, vars.projectName, vars.operator),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows'] });
      if (selectedFlowId) {
        void queryClient.invalidateQueries({ queryKey: ['flow', selectedFlowId] });
      }
      setEditModalOpen(false);
      editForm.resetFields();
      message.success('任务名称已更新');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 创建运行 Mutation ----
  const createRunMutation = useMutation({
    mutationFn: (vars: { flowId: string; body: { inputs: Record<string, unknown> } }) =>
      apiCreateFlowRun(vars.flowId, vars.body),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['flow-runs', selectedFlowId] });
      void queryClient.invalidateQueries({ queryKey: ['flow-run', data.id] });
      setRunModalOpen(false);
      runForm.resetFields();
      message.success('流程执行已创建');
      setActiveRunId(data.id);
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
      const comp = componentOptions.find((c) => c.value === values.component_name);
      if (!comp) {
        message.error('找不到选中的工具组件');
        return;
      }
      // 从组件详情获取参数定义，填充到 node.params（值为空）
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
        component_name: values.component_name,
        component_version: comp.version,
        params,
      }];
      createMutation.mutate(
        {
          display_name: values.display_name,
          department_id: (values.department_id as string) ?? null,
          project_name: (values.project_name as string) ?? null,
          operator: (values.operator as string) ?? '',
          nodes,
        },
        {
          onSuccess: async (data) => {
            // 创建成功后立即发布
            publishMutation.mutate(
              { flowId: data.id, body: { nodes } },
              {
                onSuccess: () => {
                  message.success('流程创建并发布成功');
                  setSelectedFlowId(data.id);
                },
              },
            );
          },
        },
      );
    } catch {
      // 校验失败
    }
  };

  const handleCreateRun = async (): Promise<void> => {
    if (!selectedFlowId) return;
    try {
      const values = await runForm.validateFields();
      const inputs: Record<string, unknown> = {};
      const node = (flow?.latest_version?.nodes as FlowNodeSchema[] | undefined)?.[0];
      if (node) {
        const prefix = `${node.node_id}__`;
        for (const key of Object.keys(node.params ?? {})) {
          if (key === 'experimental_object_code') continue;
          const formKey = `${prefix}${key}`;
          const artifactVal = artifactMapRef.current[formKey];
          if (artifactVal) {
            inputs[key] = artifactVal;
            continue;
          }
          const formValue = values[formKey];
          if (formValue !== undefined && formValue !== '') {
            inputs[key] = formValue;
          }
        }
      }
      createRunMutation.mutate({ flowId: selectedFlowId, body: { inputs } });
    } catch (err) {
      message.error(`参数解析失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // ---- 批量执行 ----
  const handleBatchExecute = async (): Promise<void> => {
    if (!selectedFlowId || batchFiles.length === 0) return;
    setBatchRunning(true);
    setBatchProgress({ current: 0, total: batchFiles.length, status: '开始执行...' });

    const node = (flow?.latest_version?.nodes as FlowNodeSchema[] | undefined)?.[0];
    for (let i = 0; i < batchFiles.length; i++) {
      const file = batchFiles[i];
      setBatchProgress({ current: i, total: batchFiles.length, status: `正在上传: ${file.name}` });
      try {
        // 1. 上传文件
        const uploadRes = await apiUploadFile(file);
        // 2. 构建 inputs（prompt 用组件当前活跃版本的值，不用 flow 快照）
        const inputs: Record<string, unknown> = {};
        const comp = node?.component_name ? compMap.get(node.component_name) : undefined;
        if (node) {
          for (const key of Object.keys(node.params ?? {})) {
            if (key === 'path') {
              inputs[key] = `artifact:${uploadRes.artifact_id}`;
            } else if (key === 'experimental_object_code') {
              inputs[key] = (node.params as Record<string, unknown>)?.experimental_object_code ?? '';
            } else if (key === 'prompt' && comp?.prompt) {
              inputs[key] = comp.prompt;
            } else {
              const defaultVal = (node.params as Record<string, unknown>)?.[key];
              inputs[key] = defaultVal ?? '';
            }
          }
        }
        // 3. 创建运行
        setBatchProgress({ current: i, total: batchFiles.length, status: `正在执行: ${file.name}` });
        const run = await apiCreateFlowRun(selectedFlowId, { inputs });
        // 4. 等待执行完成（轮询）
        let done = false;
        let attempts = 0;
        while (!done && attempts < 120) {
          await new Promise((r) => setTimeout(r, 2000));
          attempts++;
          const updated = await apiGetFlowRun(run.id);
          if (['succeeded', 'failed', 'cancelled'].includes(updated.status)) {
            done = true;
          }
        }
      } catch (err) {
        message.error(`文件 ${file.name} 执行失败: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    setBatchProgress({ current: batchFiles.length, total: batchFiles.length, status: '全部完成' });
    void queryClient.invalidateQueries({ queryKey: ['flow-runs', selectedFlowId] });
    setBatchRunning(false);
    setBatchFiles([]);
    setBatchModalOpen(false);
    message.success(`批量执行完成: ${batchFiles.length} 个文件`);
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
      title: '任务来源',
      key: 'department',
      width: 300,
      render: (_: unknown, record: FlowSummary) => {
        const deptName = record.department_id ? deptMap.get(record.department_id) : null;
        const projName = record.project_name;
        if (!deptName && !projName) return <Text type="secondary">-</Text>;
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {projName && (
              <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                {projName}
              </Tag>
            )}
            {projName && deptName && (
              <span style={{ color: 'var(--ocean-text-muted)', fontSize: 12 }}>&#10142;</span>
            )}
            {deptName && (
              <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                {deptName}
              </Tag>
            )}
          </div>
        );
      },
    },
    {
      title: '数据来源',
      key: 'data_source',
      width: 400,
      render: (_: unknown, record: FlowSummary) => {
        const node = (record.latest_version?.nodes ?? [])[0] as { component_name?: string } | undefined;
        const compName = node?.component_name;
        if (!compName) return <Text type="secondary">-</Text>;
        const comp = compMap.get(compName);
        const compLabel = comp?.display_name ?? compName;
        const objCode = comp?.experimental_object_code;
        const obj = objCode ? objMap.get(objCode) : null;
        const eqName = comp?.equipment_id ? equipMap.get(comp.equipment_id) : null;
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
              {compLabel}
            </Tag>
            {obj && (
              <>
                <span style={{ color: 'var(--ocean-text-muted)', fontSize: 12 }}>&#10142;</span>
                <Tag color="green" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                  {obj.display_name}
                </Tag>
              </>
            )}
            {eqName && (
              <>
                <span style={{ color: 'var(--ocean-text-muted)', fontSize: 12 }}>&#10142;</span>
                <Tag color="cyan" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                  {eqName}
                </Tag>
              </>
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
              onClick={(e) => {
                e.stopPropagation();
                setEditFlowId(record.id);
                const currentNode = (record.latest_version?.nodes ?? [])[0] as { component_name?: string } | undefined;
                editForm.setFieldsValue({
                  display_name: record.display_name,
                  code: record.code,
                  department_id: record.department_id ?? undefined,
                  project_name: record.project_name ?? undefined,
                  operator: record.operator ?? undefined,
                  component_name: currentNode?.component_name ?? undefined,
                });
                setEditModalOpen(true);
              }}
            >
              编辑
            </Button>
            <Popconfirm
              title="确定归档该流程？"
              onConfirm={(e) => {
                e?.stopPropagation();
                archiveMutation.mutate(record.id);
              }}
              okText="归档"
              cancelText="取消"
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


  const canExecute = flow?.status === 'published' || !!flow?.latest_version;

  return (
    <div>
      <Space style={{ marginBottom: 16, alignItems: 'center' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
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
              <span>运行管理</span>
              <Button
                type="primary"
                size="small"
                disabled={!canExecute}
                onClick={() => {
                  runForm.resetFields();
                  artifactMapRef.current = {};
                  // 手动设置初始值，prompt 用组件当前活跃版本的值
                  if (runNode && runParamEntries.length > 0) {
                    const comp = runNode.component_name ? compMap.get(runNode.component_name) : undefined;
                    const initialValues: Record<string, unknown> = {};
                    for (const [key, defaultVal] of runParamEntries) {
                      const formKey = `${runNode.node_id}__${key}`;
                      if (key === 'experimental_object_code') continue;
                      if (key === 'prompt' && comp?.prompt) {
                        initialValues[formKey] = comp.prompt;
                      } else {
                        initialValues[formKey] = defaultVal || '';
                      }
                    }
                    runForm.setFieldsValue(initialValues);
                  }
                  setRunModalOpen(true);
                }}
              >
                执行
              </Button>
              <Button
                size="small"
                disabled={!canExecute}
                onClick={() => {
                  setBatchFiles([]);
                  setBatchProgress(null);
                  setBatchModalOpen(true);
                }}
              >
                批量执行
              </Button>
            </Space>
          }
          style={{ marginBottom: 16 }}
        >
          {/* 运行列表 */}
          <Table<FlowRunSummary>
            columns={[
              { title: '作业 ID', dataIndex: 'job_id', key: 'job_id', width: 280, ellipsis: true,
                render: (v: string | null, record: FlowRunSummary) => {
                  if (!v) return '-';
                  const out = record.output_summary;
                  const meta = (out?._metadata ?? {}) as Record<string, unknown>;
                  const header = (meta.header ?? meta.metadata ?? {}) as Record<string, unknown>;
                  const points = (meta.points ?? []) as { name: string; value: unknown; unit: string | null }[];
                  const seriesList = (meta.series ?? []) as { name: string; columns: string[]; rows: unknown[][] }[];
                  // 构建预览内容：标头 + 前3个指标 + 序列概要
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
                      <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</Text>
                    </Tooltip>
                  );
                },
              },
              { title: '数据接口', key: 'component', width: 220,
                render: () => {
                  const node = (flow?.latest_version?.nodes ?? [])[0] as { component_name?: string } | undefined;
                  if (!node?.component_name) return <Text type="secondary">-</Text>;
                  const comp = compMap.get(node.component_name);
                  return (
                    <Space size={4}>
                      <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                        {comp?.display_name ?? node.component_name}
                      </Tag>
                      {comp?.version && (
                        <Text type="secondary" style={{ fontSize: 12 }}>v{comp.version}</Text>
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
              { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180,
                render: (v: string) => fmtTime(v) },
              { title: '耗时', key: 'duration', width: 100,
                render: (_: unknown, record: FlowRunSummary) => {
                  if (!record.started_at || !record.completed_at) return '-';
                  const ms = new Date(record.completed_at).getTime() - new Date(record.started_at).getTime();
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
                        onClick={() => {
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
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="department_id"
            label="所属单位"
            rules={[{ required: true, message: '请选择所属单位' }]}
          >
            <Select
              placeholder="请选择所属单位"
              showSearch
              optionFilterProp="label"
              options={deptOptions}
            />
          </Form.Item>
          <Form.Item
            name="component_name"
            label="数据接口"
            rules={[{ required: true, message: '请选择数据接口' }]}
          >
            <Select
              placeholder="选择数据接口"
              showSearch
              optionFilterProp="label"
              options={componentOptions}
              onChange={(value: string, option: any) => {
                const opt = option as { label?: string } | undefined;
                const now = new Date();
                const ts = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`;
                const compLabel = opt?.label ? opt.label.split(' (')[0] : value;
                createForm.setFieldsValue({
                  code: `${value}_${ts}`,
                  display_name: `${compLabel}_${ts}`,
                });
              }}
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
          <Form.Item name="project_name" label="项目名称">
            <Input placeholder="可选" maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑任务 Modal */}
      <Modal
        title="编辑任务"
        open={editModalOpen}
        onOk={async () => {
          try {
            const values = await editForm.validateFields(['display_name', 'department_id', 'project_name', 'operator', 'component_name']);
            if (editFlowId) {
              updateFlowMutation.mutate(
                {
                  flowId: editFlowId,
                  displayName: values.display_name as string,
                  departmentId: (values.department_id as string) ?? null,
                  projectName: (values.project_name as string) ?? null,
                  operator: (values.operator as string) ?? null,
                },
                {
                  onSuccess: async () => {
                    // 如果数据接口变了，重新发布
                    const newCompName = values.component_name as string | undefined;
                    const flow = editFlowId ? queryClient.getQueryData<FlowSummary>(['flow', editFlowId]) : undefined;
                    const currentNode = (flow?.latest_version?.nodes ?? [])[0] as { component_name?: string } | undefined;
                    if (newCompName && newCompName !== currentNode?.component_name) {
                      const comp = componentOptions.find((c) => c.value === newCompName);
                      if (comp) {
                        try {
                          const detail = await apiGetComponent(comp.summary.id);
                          const parsed = parseManifest(detail.manifest_yaml);
                          const params: Record<string, unknown> = {};
                          for (const p of parsed.params) {
                            params[p.name] = p.default ?? '';
                          }
                          const nodes: FlowNodeSchema[] = [{
                            node_id: 'n1',
                            component_name: newCompName,
                            component_version: comp.version,
                            params,
                          }];
                          await apiPublishFlow(editFlowId, { nodes });
                          void queryClient.invalidateQueries({ queryKey: ['flows'] });
                          void queryClient.invalidateQueries({ queryKey: ['flow', editFlowId] });
                          message.success('任务已更新并重新发布');
                        } catch (err) {
                          message.error(`重新发布失败: ${err instanceof Error ? err.message : String(err)}`);
                        }
                      }
                    }
                  },
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
            <Select
              placeholder="请选择所属单位"
              showSearch
              optionFilterProp="label"
              allowClear
              options={deptOptions}
            />
          </Form.Item>
          <Form.Item name="project_name" label="项目名称">
            <Input placeholder="可选" maxLength={200} />
          </Form.Item>
          <Form.Item
            name="operator"
            label="执行人"
            rules={[{ required: true, message: '请输入执行人' }]}
          >
            <Input placeholder="如：宋昊" maxLength={100} />
          </Form.Item>
          <Form.Item name="component_name" label="数据接口">
            <Select
              placeholder="选择数据接口"
              showSearch
              optionFilterProp="label"
              options={componentOptions}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 创建执行 Modal */}
      <Modal
        title="创建流程执行"
        open={runModalOpen}
        onOk={handleCreateRun}
        onCancel={() => {
          setRunModalOpen(false);
          runForm.resetFields();
        }}
        confirmLoading={createRunMutation.isPending}
        okText="执行"
        cancelText="取消"
        width={600}
      >
        <Form form={runForm} layout="vertical">
          {runNode && (() => {
            const runComp = runNode.component_name ? compMap.get(runNode.component_name) : undefined;
            return runComp ? (
              <div style={{ marginBottom: 16, padding: '8px 12px', background: 'var(--ocean-surface-structural)', borderRadius: 6 }}>
                <Space size={6}>
                  <Text type="secondary" style={{ fontSize: 13 }}>数据接口：</Text>
                  <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                    {runComp.display_name ?? runNode.component_name}
                  </Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>v{runComp.version}</Text>
                </Space>
              </div>
            ) : null;
          })()}
          {runNode && runParamEntries.length > 0 && (
            <div key={runNode.node_id}>
              {runParamEntries.map(([key, defaultVal]) => {
                const formKey = `${runNode.node_id}__${key}`;
                  const isPath = key === 'path';
                  const isFileEngine = key === 'file_engine';
                  // 参数标签映射
                  const labelMap: Record<string, string> = {
                    path: '实验报告文件',
                    file_engine: '文件读取方式',
                    prompt: '大模型提示词',
                  };
                  const label = labelMap[key] ?? key;

                  if (isFileEngine) {
                    // 文件读取方式：自动检测（只读提示）
                    return (
                      <Form.Item key={formKey} label={label}>
                        <Text type="secondary">自动检测（PDF/图片/Word/Excel/文本）</Text>
                      </Form.Item>
                    );
                  }

                  if (isPath) {
                    return (
                      <Form.Item key={formKey} label={label}>
                        <Input.Group compact style={{ display: 'flex' }}>
                          <Form.Item name={formKey} noStyle>
                            <Input
                              style={{ flex: 1 }}
                              placeholder="上传文件后自动填充，或手动输入路径"
                            />
                          </Form.Item>
                          <Button
                            loading={uploadLoading === formKey}
                            onClick={() => {
                              if (fileInputRef.current) {
                                fileInputRef.current.dataset.formkey = formKey;
                                fileInputRef.current.click();
                              }
                            }}
                          >
                            上传
                          </Button>
                        </Input.Group>
                      </Form.Item>
                    );
                  }

                  // 其余参数（含 prompt）—— prompt 用当前活跃组件版本的值，不用 flow 快照
                  const displayVal = key === 'prompt' && runNode?.component_name
                    ? (compMap.get(runNode.component_name)?.prompt ?? defaultVal)
                    : defaultVal;
                  return (
                    <Form.Item
                      key={formKey}
                      name={formKey}
                      label={label}
                      initialValue={displayVal || ''}
                    >
                      <Input.TextArea
                        rows={key === 'prompt' ? 6 : 1}
                        placeholder={defaultVal ? String(defaultVal) : `输入 ${key}`}
                      />
                    </Form.Item>
                  );
                })}
            </div>
          )}
        </Form>
      </Modal>

      {/* 批量执行 Modal */}
      <Modal
        title="批量执行"
        open={batchModalOpen}
        onCancel={() => {
          if (!batchRunning) {
            setBatchModalOpen(false);
            setBatchFiles([]);
            setBatchProgress(null);
          }
        }}
        footer={
          batchRunning ? null : (
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
        ) : (
          <>
            {runNode && (() => {
              const runComp = runNode.component_name ? compMap.get(runNode.component_name) : undefined;
              return runComp ? (
                <div style={{ marginBottom: 16, padding: '8px 12px', background: 'var(--ocean-surface-structural)', borderRadius: 6 }}>
                  <Space size={6}>
                    <Text type="secondary" style={{ fontSize: 13 }}>数据接口：</Text>
                    <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                      {runComp.display_name ?? runNode.component_name}
                    </Tag>
                    <Text type="secondary" style={{ fontSize: 12 }}>v{runComp.version}</Text>
                  </Space>
                </div>
              ) : null;
            })()}
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
            <Text type="secondary" style={{ fontSize: 12 }}>
              将使用当前任务的数据接口，逐个上传文件并执行。文件合规性由用户自行负责。
            </Text>
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
          setUploadLoading(formKey);
          try {
            const res = await apiUploadFile(file);
            runForm.setFieldValue(formKey, file.name);
            artifactMapRef.current[formKey] = `artifact:${res.artifact_id}`;
            message.success(`文件已上传: ${file.name}`);
          } catch (err) {
            message.error(`上传失败: ${err instanceof Error ? err.message : String(err)}`);
          } finally {
            setUploadLoading(null);
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
