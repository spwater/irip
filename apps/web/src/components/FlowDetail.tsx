import { useState, useRef } from 'react';
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCancelFlowRun,
  apiCreateFlow,
  apiCreateFlowRun,
  apiDeleteFlow,
  apiDeleteFlowRun,
  apiUploadFile,
  apiArchiveFlow,
  apiRestoreFlow,
  apiGetComponent,
  apiGetFlow,
  apiListComponents,
  apiListObjects,
  apiListEquipment,
  apiListDepartments,
  apiListFlows,
  apiListFlowRuns,
  apiPublishFlow,
  apiResumeFlowRun,
  apiUpdateFlow,
  extractApiError,
  type ComponentSummary,
  type FlowNodeSchema,
  type FlowRunSummary,
  type FlowSummary,
  type IndustrialObject,
} from '@/api/client';
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
  const [artifactMap, setArtifactMap] = useState<Record<string, string>>({});
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [createForm] = Form.useForm();
  const [runForm] = Form.useForm();

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

  // ---- 流程列表查询 ----
  const [showArchived, setShowArchived] = useState(false);
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const { data: listData, isLoading: listLoading } = useQuery({
    queryKey: ['flows'],
    queryFn: () => apiListFlows(),
  });

  const allFlows: FlowSummary[] = listData?.items ?? [];
  let flows: FlowSummary[] = showArchived
    ? allFlows
    : allFlows.filter((f) => f.status !== 'deprecated');
  if (deptFilter) {
    flows = flows.filter((f) => f.department_id === deptFilter);
  }

  // ---- 查询组件列表、实验对象、设备，用于在流程列表展示关联信息 ----
  const { data: compListData } = useQuery({
    queryKey: ['components-for-flow-list'],
    queryFn: () => apiListComponents(),
  });
  // component_name → ComponentSummary（取最新版本）
  const compMap = new Map<string, ComponentSummary>();
  for (const c of compListData?.items ?? []) {
    const existing = compMap.get(c.name);
    if (!existing || c.version > existing.version) {
      compMap.set(c.name, c);
    }
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

  // ---- 选中流程详情查询 ----
  const { data: flow } = useQuery({
    queryKey: ['flow', selectedFlowId],
    queryFn: () => apiGetFlow(selectedFlowId!),
    enabled: !!selectedFlowId,
  });

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
      void queryClient.invalidateQueries({ queryKey: ['flow', selectedFlowId] });
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
    mutationFn: (vars: { flowId: string; displayName: string; departmentId?: string | null; projectName?: string | null }) =>
      apiUpdateFlow(vars.flowId, vars.displayName, vars.departmentId, vars.projectName),
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
      void queryClient.invalidateQueries({ queryKey: ['flow-runs', selectedFlowId] });
      void queryClient.invalidateQueries({ queryKey: ['flow-run', activeRunId] });
      message.success('流程执行已完成');
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
        { code: values.code, display_name: values.display_name, nodes },
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
      // 从表单收集参数值，构建 inputs
      const inputs: Record<string, unknown> = {};
      const nodeParams = flow?.latest_version?.nodes as FlowNodeSchema[] | undefined;
      if (nodeParams) {
        for (const node of nodeParams) {
          const prefix = `${node.node_id}__`;
          for (const key of Object.keys(node.params ?? {})) {
            const formKey = `${prefix}${key}`;
            const formValue = values[formKey];
            if (formValue !== undefined && formValue !== '') {
              // 如果是上传的文件，用真实 artifact 值替换显示的文件名
              inputs[key] = artifactMap[formKey] ?? formValue;
            }
          }
        }
      }
      // 也保留原始 JSON 输入
      if (values.inputs_json) {
        const jsonInputs = JSON.parse(values.inputs_json as string);
        Object.assign(inputs, jsonInputs);
      }
      createRunMutation.mutate({ flowId: selectedFlowId, body: { inputs } });
    } catch (err) {
      message.error(`参数解析失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // ---- 表格列定义 ----
  const flowColumns: ColumnsType<FlowSummary> = [
    {
      title: '名称',
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
      title: '任务来源',
      key: 'department',
      width: 240,
      render: (_: unknown, record: FlowSummary) => {
        if (!record.department_id) return <Text type="secondary">-</Text>;
        const deptName = deptMap.get(record.department_id);
        return (
          <div>
            {deptName ? <Text>{deptName}</Text> : <Text type="secondary">-</Text>}
            {record.project_name && (
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>{record.project_name}</Text>
              </div>
            )}
          </div>
        );
      },
    },
    {
      title: '实验对象',
      key: 'exp_objects',
      width: 200,
      render: (_: unknown, record: FlowSummary) => {
        const nodes = (record.latest_version?.nodes ?? []) as { component_name?: string }[];
        const compNames = Array.from(
          new Set(nodes.map((n) => n.component_name).filter((n): n is string => Boolean(n))),
        );
        // 从组件的 experimental_object_code 收集实验对象编码
        const objCodes = Array.from(
          new Set(
            compNames
              .map((name) => compMap.get(name)?.experimental_object_code)
              .filter((c): c is string => Boolean(c)),
          ),
        );
        if (objCodes.length === 0) return <Text type="secondary">-</Text>;
        return (
          <div>
            {objCodes.map((code) => {
              const obj = objMap.get(code);
              if (!obj) return <div key={code}><Text code>{code}</Text></div>;
              const eqName = obj.equipment_id ? equipMap.get(obj.equipment_id) : null;
              return (
                <div key={code}>
                  <Text>{obj.display_name}</Text>
                  {eqName && (
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>{eqName}</Text>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        );
      },
    },
    {
      title: '数据接口',
      key: 'components',
      width: 180,
      render: (_: unknown, record: FlowSummary) => {
        const nodes = (record.latest_version?.nodes ?? []) as { component_name?: string }[];
        const compNames = Array.from(
          new Set(nodes.map((n) => n.component_name).filter((n): n is string => Boolean(n))),
        );
        if (compNames.length === 0) return <Text type="secondary">-</Text>;
        return (
          <div>
            {compNames.map((name) => {
              const comp = compMap.get(name);
              return (
                <div key={name}>
                  <Text>{comp?.display_name ?? name}</Text>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>{name}</Text>
                  </div>
                </div>
              );
            })}
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
                editForm.setFieldsValue({
                  display_name: record.display_name,
                  code: record.code,
                  department_id: record.department_id ?? undefined,
                  project_name: record.project_name ?? undefined,
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
        <Button type="primary" onClick={() => setCreateModalOpen(true)}>
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
          placeholder="执行实验部门筛选"
          style={{ width: 200 }}
          value={deptFilter ?? '__all__'}
          onChange={(val: string) => setDeptFilter(val === '__all__' ? undefined : val)}
          options={[{ value: '__all__', label: '全部' }, ...deptOptions]}
        />
      </Space>

      {/* 任务列表 */}
      <Card title="任务列表" style={{ marginBottom: 16 }}>
        <Table<FlowSummary>
          columns={flowColumns}
          dataSource={flows}
          rowKey="id"
          loading={listLoading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
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
                  setRunModalOpen(true);
                }}
              >
                执行
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
                  const header = (meta.header ?? {}) as Record<string, unknown>;
                  const previewText = Object.keys(header).length > 0
                    ? JSON.stringify(header, null, 2)
                    : '';
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
              { title: '摘要', key: 'summary', width: 120, ellipsis: true,
                render: (_: unknown, record: FlowRunSummary) => {
                  const out = record.output_summary;
                  if (!out) return '-';
                  const meta = (out._metadata ?? {}) as Record<string, unknown>;
                  const rows = (meta.all_rows ?? meta.preview_rows ?? []) as Record<string, unknown>[];
                  return rows.length > 0 ? `${rows.length} 行` : '-';
                },
              },
              { title: '状态', dataIndex: 'status', key: 'status', width: 100,
                render: (s: string) => <Tag color={RUN_STATUS_COLOR[s] ?? 'default'}>{RUN_STATUS_LABEL[s] ?? s}</Tag> },
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
                    {record.status === 'pending' && (
                      <Button type="link" size="small"
                        loading={resumeMutation.isPending}
                        onClick={() => resumeMutation.mutate(record.id)}>
                        执行
                      </Button>
                    )}
                    {activeRunId === record.id && record.status !== 'pending' && record.status !== 'succeeded' && record.status !== 'cancelled' && (
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
            pagination={{ pageSize: 10, showSizeChanger: false }}
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
            label="执行实验部门"
            rules={[{ required: true, message: '请选择执行实验部门' }]}
          >
            <Select
              placeholder="请选择执行实验部门"
              showSearch
              optionFilterProp="label"
              options={deptOptions}
            />
          </Form.Item>
          <Form.Item
            name="component_name"
            label="选择工具"
            rules={[{ required: true, message: '请选择工具' }]}
          >
            <Select
              placeholder="选择工具组件"
              showSearch
              optionFilterProp="label"
              options={componentOptions}
              onChange={(value: string) => {
                const now = new Date();
                const ts = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`;
                const tsDisplay = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
                createForm.setFieldsValue({
                  code: `${value}_${ts}`,
                  display_name: `${value} 流程 ${tsDisplay}`,
                });
              }}
            />
          </Form.Item>
          <Form.Item
            name="code"
            label="流程编码"
            rules={[
              { required: true, message: '请输入流程编码' },
              {
                pattern: /^[a-z][a-z0-9_]*$/,
                message: '仅小写字母/数字/下划线，首字符必须为字母',
              },
            ]}
          >
            <Input placeholder="如：grate_cooler_pipeline" maxLength={64} />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="流程名称"
            rules={[{ required: true, message: '请输入流程名称' }]}
          >
            <Input placeholder="如：篦冷机分析流程" maxLength={200} />
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
            const values = await editForm.validateFields(['display_name', 'department_id', 'project_name']);
            if (editFlowId) {
              updateFlowMutation.mutate({
                flowId: editFlowId,
                displayName: values.display_name as string,
                departmentId: (values.department_id as string) ?? null,
                projectName: (values.project_name as string) ?? null,
              });
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
          <Form.Item name="code" label="流程编码">
            <Input disabled />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="流程名称"
            rules={[{ required: true, message: '请输入流程名称' }]}
          >
            <Input placeholder="请输入流程名称" maxLength={200} />
          </Form.Item>
          <Form.Item name="department_id" label="执行实验部门">
            <Select
              placeholder="请选择执行实验部门"
              showSearch
              optionFilterProp="label"
              allowClear
              options={deptOptions}
            />
          </Form.Item>
          <Form.Item name="project_name" label="项目名称">
            <Input placeholder="可选" maxLength={200} />
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
          {(flow?.latest_version?.nodes as FlowNodeSchema[] | undefined)?.map((node) => {
            const params = node.params as Record<string, unknown> ?? {};
            const paramEntries = Object.entries(params).filter(
              ([key]) => key !== 'experimental_object_code',
            );
            if (paramEntries.length === 0) return null;
            // 控制参数渲染顺序：path → file_engine → prompt → 其余
            const orderedKeys = ['path', 'file_engine', 'prompt'];
            const sortedEntries = [...paramEntries].sort((a, b) => {
              const ai = orderedKeys.indexOf(a[0]);
              const bi = orderedKeys.indexOf(b[0]);
              if (ai >= 0 && bi >= 0) return ai - bi;
              if (ai >= 0) return -1;
              if (bi >= 0) return 1;
              return 0;
            });
            return (
              <div key={node.node_id}>
                {sortedEntries.map(([key, defaultVal]) => {
                  const formKey = `${node.node_id}__${key}`;
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
                    // 文件读取方式：水平按钮组
                    return (
                      <div key={formKey} style={{ marginBottom: 24 }}>
                        <div style={{ display: 'inline-block', marginRight: 12, lineHeight: '32px', fontWeight: 500 }}>
                          {label}
                        </div>
                        <Form.Item
                          name={formKey}
                          initialValue={defaultVal || 'pymupdf'}
                          style={{ display: 'inline-block', marginBottom: 0 }}
                        >
                          <Radio.Group
                            optionType="button"
                            buttonStyle="solid"
                            options={[
                              { value: 'pymupdf', label: 'pymupdf' },
                              { value: 'image', label: 'image' },
                              { value: 'raw', label: 'raw' },
                            ]}
                          />
                        </Form.Item>
                      </div>
                    );
                  }

                  if (isPath) {
                    return (
                      <Form.Item
                        key={formKey}
                        name={formKey}
                        label={label}
                        initialValue={defaultVal || ''}
                      >
                        <Input.Group compact style={{ display: 'flex' }}>
                          <Form.Item name={formKey} noStyle>
                            <Input
                              style={{ flex: 1 }}
                              placeholder={defaultVal ? String(defaultVal) : `输入 ${key}`}
                            />
                          </Form.Item>
                          <Button
                            loading={uploadLoading === formKey}
                            onClick={() => fileInputRef.current?.click()}
                          >
                            上传
                          </Button>
                          <input
                            ref={fileInputRef}
                            type="file"
                            style={{ display: 'none' }}
                            onChange={async (e) => {
                              const file = e.target.files?.[0];
                              if (!file) return;
                              setUploadLoading(formKey);
                              try {
                                const res = await apiUploadFile(file);
                                runForm.setFieldValue(formKey, file.name);
                                setArtifactMap((prev) => ({ ...prev, [formKey]: `artifact:${res.artifact_id}` }));
                                message.success(`文件已上传: ${file.name}`);
                              } catch (err) {
                                message.error(`上传失败: ${err instanceof Error ? err.message : String(err)}`);
                              } finally {
                                setUploadLoading(null);
                                if (fileInputRef.current) fileInputRef.current.value = '';
                              }
                            }}
                          />
                        </Input.Group>
                      </Form.Item>
                    );
                  }

                  // 其余参数（含 prompt）
                  return (
                    <Form.Item
                      key={formKey}
                      name={formKey}
                      label={label}
                      initialValue={defaultVal || ''}
                    >
                      <Input.TextArea
                        rows={key === 'prompt' ? 6 : 1}
                        placeholder={defaultVal ? String(defaultVal) : `输入 ${key}`}
                      />
                    </Form.Item>
                  );
                })}
              </div>
            );
          })}
          <Form.Item name="inputs_json" label="额外参数 (JSON，可选)">
            <Input.TextArea
              rows={3}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder={`{\n  "key": "value"\n}`}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

export default FlowDetail;
