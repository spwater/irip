import { useState, useEffect } from 'react';
import {
  Button,
  Card,
  Checkbox,
  Descriptions,
  Divider,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient, useQueries } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCancelFlowRun,
  apiCreateFlow,
  apiCreateFlowRun,
  apiDeleteFlowRun,
  apiBrowseFiles,
  apiArchiveFlow,
  apiRestoreFlow,
  apiPersistRunAsFact,
  apiGetComponent,
  apiGetFlow,
  apiGetFlowRun,
  apiListComponents,
  apiListObjects,
  apiListFlows,
  apiListFlowRuns,
  apiPublishFlow,
  apiResumeFlowRun,
  apiRetryFlowNode,
  extractApiError,
  type BrowseResponse,
  type ComponentDetail,
  type ComponentSummary,
  type FlowEdgeSchema,
  type FlowNodeExecution,
  type FlowNodeSchema,
  type FlowRunDetail,
  type FlowRunSummary,
  type FlowSummary,
} from '@/api/client';

const { Title, Text, Paragraph } = Typography;

/** 流程状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  draft: 'blue',
  published: 'green',
  deprecated: 'default',
};

/** 流程状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  deprecated: '已弃用',
};

/** 把 UTC 时间字符串转成本地时间显示 */
function fmtTime(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString('zh-CN', { hour12: false });
}

/** 运行状态 → 颜色 */
const RUN_STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  succeeded: 'green',
  failed: 'red',
  cancelled: 'orange',
  paused: 'gold',
};

/** 运行状态 → 中文标签 */
const RUN_STATUS_LABEL: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  paused: '已暂停',
};

/** 节点状态 → 颜色 */
const NODE_STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  succeeded: 'green',
  failed: 'red',
  skipped: 'orange',
};

/** 节点状态 → 中文标签 */
const NODE_STATUS_LABEL: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  skipped: '已跳过',
};

// ============================================================
// 组件分类 & 可视化节点构建器
// ============================================================

/** LLM 驱动的组件名称集合 — 摩登组件 */
const LLM_COMPONENTS = new Set(['ez_scan_extractor']);

/** 组件类别 → 中文标签 */
const KIND_LABEL: Record<string, string> = {
  ingestion: '数据接入',
  transform: '数据转换',
  quality: '质量校验',
  statistics: '统计分析',
  output: '结果输出',
  model: '模型推理',
};

/** 解析出的参数定义 */
type ParamDef = {
  name: string;
  type: string;
  required: boolean;
  default: unknown;
  description: string;
};

/** 解析出的端口定义 */
type PortDef = {
  name: string;
  type: string;
};

/** 解析出的 manifest 结构 */
type ParsedManifest = {
  params: ParamDef[];
  inputs: PortDef[];
  outputs: PortDef[];
};

/** 可视化节点构建器中的节点项 */
type VisualNode = {
  key: string;
  node_id: string;
  component_name: string;
  component_version: string;
  component_id: string;
  params: Record<string, unknown>;
  paramsJson: string;
  useJsonParams: boolean;
};

/** 可视化边构建器中的边项 */
type VisualEdge = {
  key: string;
  source_node: string;
  source_port: string;
  target_node: string;
  target_port: string;
};

/** 生成唯一 key */
let _keyCounter = 0;
function genKey(): string {
  _keyCounter += 1;
  return `vk_${Date.now()}_${_keyCounter}`;
}

/** 将 YAML 中的标量值字符串转换为对应的 JS 类型 */
function parseScalarValue(value: string): unknown {
  const v = value.trim();
  if (v === '' || v === '""' || v === "''") return '';
  if (v === 'true') return true;
  if (v === 'false') return false;
  if (v === 'null' || v === '~') return null;
  const num = Number(v);
  if (!isNaN(num) && v !== '') return num;
  return v.replace(/^["']|["']$/g, '');
}

/** 判断参数类型是否为复杂类型（需要 JSON 输入） */
function isComplexType(type: string): boolean {
  const t = type.toLowerCase();
  return t === 'array' || t === 'object' || t === 'dict' || t === 'list' || t === 'map';
}

/** 将表单输入字符串转换为参数类型对应的值 */
function convertParamValue(value: string, type: string): unknown {
  const t = type.toLowerCase();
  if (t === 'int' || t === 'integer' || t === 'long') {
    return value === '' ? '' : Math.round(Number(value));
  }
  if (t === 'float' || t === 'double' || t === 'number') {
    return value === '' ? '' : Number(value);
  }
  if (t === 'bool' || t === 'boolean') {
    return value === 'true' || value === '1';
  }
  return value;
}

/**
 * 从 manifest_yaml 中解析参数定义和端口定义。
 * 采用简单的行解析，不依赖 js-yaml 库。
 *
 * manifest 中的参数使用 JSON Schema 格式：
 *   parameters:
 *     type: object
 *     required: [path, prompt]
 *     properties:
 *       path:
 *         type: string
 *         description: "文件路径"
 *       timeout:
 *         type: integer
 *         default: 60
 *
 * 端口使用列表格式：
 *   inputs:
 *     - name: observations
 *       data_type: observation_table
 *   outputs:
 *     - name: statistics
 *       data_type: statistics_result
 */
function parseManifest(manifestYaml: string): ParsedManifest {
  const result: ParsedManifest = { params: [], inputs: [], outputs: [] };
  if (!manifestYaml || typeof manifestYaml !== 'string') return result;

  const lines = manifestYaml.split('\n');

  /** 找到顶层 key 所在的行号 */
  const findTopKey = (key: string): number => {
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();
      if (trimmed === `${key}:` || trimmed.startsWith(`${key}:`)) {
        if (!line.startsWith(' ') && !line.startsWith('\t')) {
          return i;
        }
      }
    }
    return -1;
  };

  /** 找到 start 行之后下一个顶层 key 的行号 */
  const findNextTopKey = (start: number): number => {
    for (let i = start + 1; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();
      if (trimmed && !trimmed.startsWith('#') && !line.startsWith(' ') && !line.startsWith('\t')) {
        return i;
      }
    }
    return lines.length;
  };

  // ---- 解析 inputs / outputs ----
  const parsePorts = (sectionName: 'inputs' | 'outputs'): PortDef[] => {
    const ports: PortDef[] = [];
    const start = findTopKey(sectionName);
    if (start < 0) return ports;

    // 检查是否为内联空列表（如 inputs: []）
    const lineContent = lines[start].trim();
    if (/\[\s*\]/.test(lineContent)) return ports;

    const end = findNextTopKey(start);
    const sectionLines = lines.slice(start + 1, end);
    let currentPort: PortDef | null = null;

    for (const line of sectionLines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;

      if (trimmed.startsWith('- ')) {
        if (currentPort) ports.push(currentPort);
        const content = trimmed.slice(2).trim();
        const nameMatch = content.match(/^name:\s*(.*)$/);
        currentPort = {
          name: nameMatch
            ? nameMatch[1].trim().replace(/^["']|["']$/g, '')
            : content.replace(/^["']|["']$/g, ''),
          type: '',
        };
      } else if (currentPort) {
        const match = trimmed.match(/^([\w_]+)\s*:\s*(.*)$/);
        if (match) {
          const [, key, val] = match;
          if (key === 'data_type' || key === 'type') {
            currentPort.type = val.trim().replace(/^["']|["']$/g, '');
          }
        }
      }
    }
    if (currentPort) ports.push(currentPort);
    return ports;
  };

  result.inputs = parsePorts('inputs');
  result.outputs = parsePorts('outputs');

  // ---- 解析 parameters（JSON Schema 格式）----
  const paramsStart = findTopKey('parameters');
  if (paramsStart >= 0) {
    const paramsEnd = findNextTopKey(paramsStart);
    const sectionLines = lines.slice(paramsStart + 1, paramsEnd);

    let baseIndent = -1;
    const requiredParams = new Set<string>();
    let propertiesStart = -1;

    // 第一遍：找到 required 列表和 properties 位置
    for (let i = 0; i < sectionLines.length; i++) {
      const line = sectionLines[i];
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const indent = line.length - line.trimStart().length;
      if (baseIndent < 0) baseIndent = indent;

      if (indent === baseIndent) {
        if (trimmed.startsWith('required:')) {
          // 先尝试解析同行内联列表格式：required: [a, b, c]
          const inlineMatch = trimmed.match(/required:\s*\[(.*)\]/);
          if (inlineMatch) {
            inlineMatch[1].split(',').forEach((item) => {
              const cleanItem = item.trim().replace(/^["']|["']$/g, '');
              if (cleanItem) requiredParams.add(cleanItem);
            });
          } else {
            // 块列表格式：
            // required:
            //   - a
            //   - b
            for (let j = i + 1; j < sectionLines.length; j++) {
              const reqLine = sectionLines[j];
              const reqTrimmed = reqLine.trim();
              if (!reqTrimmed || reqTrimmed.startsWith('#')) continue;
              const reqIndent = reqLine.length - reqLine.trimStart().length;
              if (reqIndent <= baseIndent) break;
              const reqMatch = reqTrimmed.match(/^-\s*(.*)$/);
              if (reqMatch) {
                requiredParams.add(reqMatch[1].trim().replace(/^["']|["']$/g, ''));
              }
            }
          }
        } else if (trimmed.startsWith('properties:')) {
          propertiesStart = i;
        }
      }
    }

    // 第二遍：解析 properties 下的参数定义
    if (propertiesStart >= 0) {
      let propsIndent = -1;
      let propIndent = -1;
      let currentParam: ParamDef | null = null;

      for (let i = propertiesStart + 1; i < sectionLines.length; i++) {
        const line = sectionLines[i];
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const indent = line.length - line.trimStart().length;

        if (propsIndent < 0) propsIndent = indent;

        if (indent <= propsIndent) {
          // 新的参数 key
          const match = trimmed.match(/^([\w_]+)\s*:\s*(.*)$/);
          if (match) {
            if (currentParam) result.params.push(currentParam);
            currentParam = {
              name: match[1],
              type: 'string',
              required: requiredParams.has(match[1]),
              default: undefined,
              description: '',
            };
            propIndent = -1; // 重置属性缩进
          }
        } else if (currentParam) {
          // 当前参数的属性 — 只处理直接子级
          if (propIndent < 0) propIndent = indent;
          if (indent === propIndent) {
            const match = trimmed.match(/^([\w_]+)\s*:\s*(.*)$/);
            if (match) {
              const [, key, val] = match;
              const cleanVal = val.trim().replace(/^["']|["']$/g, '');
              switch (key) {
                case 'type':
                  currentParam.type = cleanVal;
                  break;
                case 'default':
                  currentParam.default = parseScalarValue(cleanVal);
                  break;
                case 'description':
                  currentParam.description = cleanVal;
                  break;
                default:
                  break;
              }
            }
          }
          // 更深层级的嵌套属性（如 items.properties）被忽略
        }
      }
      if (currentParam) result.params.push(currentParam);
    }
  }

  return result;
}

/**
 * 流程编排页面（IRIP V2-T05）
 *
 * 功能：
 * - 流程列表 Table（编码 / 名称 / 状态 / 最新版本）
 * - 顶部「新建流程」按钮 → Modal（编码 + 名称）
 * - 选中流程 → 展示基本信息 + 运行操作（执行 / 继续 / 取消）
 * - 节点执行列表 Table（节点 ID / 状态 / 耗时）
 * - 发布版本 Modal（可视化节点构建器 + 高级 JSON 模式）
 */
export function FlowDetail(): JSX.Element {
  const queryClient = useQueryClient();
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [publishModalOpen, setPublishModalOpen] = useState(false);
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [runFileBrowserOpen, setRunFileBrowserOpen] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [createForm] = Form.useForm();
  const [runForm] = Form.useForm();

  // ---- 流程列表查询 ----
  const [showArchived, setShowArchived] = useState(false);
  const { data: listData, isLoading: listLoading } = useQuery({
    queryKey: ['flows'],
    queryFn: () => apiListFlows(),
  });

  const allFlows: FlowSummary[] = listData?.items ?? [];
  const flows: FlowSummary[] = showArchived
    ? allFlows
    : allFlows.filter((f) => f.status !== 'deprecated');

  // ---- 选中流程详情查询 ----
  const { data: flow, isLoading: flowLoading } = useQuery({
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

  // ---- 运行详情查询 ----
  const { data: runDetail, isLoading: runLoading } = useQuery({
    queryKey: ['flow-run', activeRunId],
    queryFn: () => apiGetFlowRun(activeRunId!),
    enabled: !!activeRunId,
  });

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
      setPublishModalOpen(false);
      message.success('流程版本发布成功');
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

  // ---- 重试节点 Mutation ----
  const retryMutation = useMutation({
    mutationFn: (vars: { runId: string; nodeId: string }) =>
      apiRetryFlowNode(vars.runId, vars.nodeId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-run', activeRunId] });
      message.success('节点已重试');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 事件处理 ----
  const handleCreate = async (): Promise<void> => {
    try {
      const values = await createForm.validateFields();
      createMutation.mutate({
        code: values.code,
        display_name: values.display_name,
      });
    } catch {
      // 校验失败
    }
  };

  const handlePublish = (body: {
    nodes: FlowNodeSchema[];
    edges: FlowEdgeSchema[];
    random_seed: number;
  }): void => {
    if (!selectedFlowId) return;
    publishMutation.mutate({ flowId: selectedFlowId, body });
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
              // 检查这个参数是否需要跨节点共享
              // 当前简单处理：所有节点共用同名参数
              inputs[key] = formValue;
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
    { title: '编码', dataIndex: 'code', key: 'code', width: 180 },
    { title: '名称', dataIndex: 'display_name', key: 'display_name' },
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
      title: '最新版本',
      key: 'latest_version',
      width: 120,
      render: (_: unknown, record: FlowSummary) =>
        record.latest_version ? `v${record.latest_version.version}` : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: FlowSummary) => (
        <Button
          type="link"
          size="small"
          onClick={(e) => {
            e.stopPropagation();
            setSelectedFlowId(record.id);
          }}
        >
          查看
        </Button>
      ),
    },
  ];

  const nodeColumns: ColumnsType<FlowNodeExecution> = [
    { title: '节点 ID', dataIndex: 'node_id', key: 'node_id', width: 140 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (v: string) => (
        <Tag color={NODE_STATUS_COLOR[v] ?? 'default'}>
          {NODE_STATUS_LABEL[v] ?? v}
        </Tag>
      ),
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 70,
      render: (v: number | null) => (v != null ? `${v}ms` : '-'),
    },
    {
      title: '摘要',
      key: 'summary',
      width: 200,
      ellipsis: true,
      render: (_: unknown, record: FlowNodeExecution) => {
        const out = record.output_summary;
        if (!out) return '-';
        return out._summary_text || '-';
      },
    },
    {
      title: '输出数据',
      key: 'output',
      render: (_: unknown, record: FlowNodeExecution) => {
        const out = record.output_summary;
        if (!out) {
          // 看看是否有错误
          const diag = record.diagnostics;
          if (diag && diag.error_message) {
            return <Text type="danger" style={{ fontSize: 12 }}>{String(diag.error_message)}</Text>;
          }
          return '-';
        }
        // 提取输出端口数据（排除 _metadata 和 _summary_text）
        const entries = Object.entries(out)
          .filter(([k]) => k !== '_metadata' && k !== '_summary_text');
        if (entries.length === 0) return '-';

        return (
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {entries.map(([portName, portValue]) => {
              let displayValue: string;
              if (portName === 'statistics') {
                // 统计组件输出特殊处理
                const stats = portValue as Record<string, Record<string, number>>;
                displayValue = Object.entries(stats)
                  .map(([col, vals]) => `${col}: mean=${vals.mean?.toFixed(3)}, std=${vals.std?.toFixed(3)}, median=${vals.median?.toFixed(3)}`)
                  .join('\n');
              } else if (typeof portValue === 'string') {
                displayValue = portValue.length > 200 ? portValue.slice(0, 200) + '...' : portValue;
              } else {
                displayValue = JSON.stringify(portValue, null, 2);
                if (displayValue.length > 300) displayValue = displayValue.slice(0, 300) + '...';
              }
              return (
                <div key={portName}>
                  <Text type="secondary" style={{ fontSize: 11, fontWeight: 500 }}>{portName}:</Text>
                  <pre style={{ fontSize: 11, margin: '2px 0', padding: '4px 8px', background: '#f5f5f5', borderRadius: 4, overflow: 'auto', maxHeight: 120 }}>
                    {displayValue}
                  </pre>
                </div>
              );
            })}
          </Space>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: unknown, record: FlowNodeExecution) =>
        record.status === 'failed' ? (
          <Button
            type="link"
            size="small"
            onClick={() =>
              activeRunId && retryMutation.mutate({ runId: activeRunId, nodeId: record.node_id })
            }
          >
            重试
          </Button>
        ) : null,
    },
  ];

  const canExecute = flow?.status === 'published' || !!flow?.latest_version;

  return (
    <div>
      <Title level={2}>流程编排</Title>

      <Space style={{ marginBottom: 16, alignItems: 'center' }}>
        <Button type="primary" onClick={() => setCreateModalOpen(true)}>
          新建流程
        </Button>
        <Checkbox
          checked={showArchived}
          onChange={(e) => setShowArchived(e.target.checked)}
        >
          显示已归档
        </Checkbox>
      </Space>

      {/* 流程列表 */}
      <Card title="流程列表" style={{ marginBottom: 16 }}>
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

      {/* 流程详情 */}
      {selectedFlowId && (
        <Card title="流程详情" style={{ marginBottom: 16 }} loading={flowLoading}>
          {flow && (
            <>
              <Descriptions bordered column={2} size="small">
                <Descriptions.Item label="编码">
                  <Text strong>{flow.code}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="名称">{flow.display_name}</Descriptions.Item>
                <Descriptions.Item label="最新版本">
                  {flow.latest_version
                    ? `v${flow.latest_version.version} (${flow.latest_version.digest.slice(0, 12)}…)`
                    : '未发布'}
                </Descriptions.Item>
                <Descriptions.Item label="创建时间">{fmtTime(flow.created_at)}</Descriptions.Item>
              </Descriptions>

              <Space style={{ marginTop: 16 }} wrap>
                <Button
                  onClick={() => setPublishModalOpen(true)}
                >
                  发布版本
                </Button>
                <Button
                  type="primary"
                  disabled={!canExecute}
                  onClick={() => {
                    runForm.resetFields();
                    setRunModalOpen(true);
                  }}
                >
                  创建执行
                </Button>
                {!canExecute && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    需先发布版本才能执行
                  </Text>
                )}
                {flow.status === 'deprecated' ? (
                  <Popconfirm
                    title="确定恢复该流程？"
                    description="恢复后流程将重新显示在活跃列表中"
                    onConfirm={() => selectedFlowId && restoreMutation.mutate(selectedFlowId)}
                    okText="恢复"
                    cancelText="取消"
                  >
                    <Button
                      loading={restoreMutation.isPending}
                    >
                      恢复
                    </Button>
                  </Popconfirm>
                ) : (
                  <Popconfirm
                    title="确定归档该流程？"
                    description="归档后流程将标记为已弃用，不再显示在活跃列表中"
                    onConfirm={() => selectedFlowId && archiveMutation.mutate(selectedFlowId)}
                    okText="归档"
                    cancelText="取消"
                  >
                    <Button
                      danger
                      loading={archiveMutation.isPending}
                    >
                      归档
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            </>
          )}
        </Card>
      )}

      {/* 运行管理 */}
      {selectedFlowId && (
        <Card title="运行管理" style={{ marginBottom: 16 }}>
          {/* 运行列表 */}
          <Table<FlowRunSummary>
            columns={[
              { title: '作业 ID', dataIndex: 'job_id', key: 'job_id', width: 280, ellipsis: true,
                render: (v: string | null) => v ? <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</Text> : '-' },
              { title: '状态', dataIndex: 'status', key: 'status', width: 100,
                render: (s: string) => <Tag color={RUN_STATUS_COLOR[s] ?? 'default'}>{RUN_STATUS_LABEL[s] ?? s}</Tag> },
              { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180,
                render: (v: string) => fmtTime(v) },
              { title: '完成时间', dataIndex: 'completed_at', key: 'completed_at', width: 180,
                render: (v: string | null) => fmtTime(v) },
              { title: '操作', key: 'action', width: 200,
                render: (_: unknown, record: FlowRunSummary) => (
                  <Space size="small">
                    <Button type="link" size="small"
                      onClick={() => setActiveRunId(record.id)}>
                      查看详情
                    </Button>
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

          {/* 选中运行的详情 */}
          {activeRunId ? (
            runLoading ? (
              <div style={{ textAlign: 'center', padding: 24 }}>
                <Spin />
              </div>
            ) : runDetail ? (
              <RunDetailPanel run={runDetail} nodeColumns={nodeColumns} />
            ) : (
              <Empty description="未找到运行记录" />
            )
          ) : (
            <Empty description="点击上方查看详情按钮加载执行详情" />
          )}
        </Card>
      )}

      {/* 新建流程 Modal */}
      <Modal
        title="新建流程"
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
        </Form>
      </Modal>

      {/* 发布版本 Modal — 可视化节点构建器 */}
      <PublishVersionModal
        open={publishModalOpen}
        onClose={() => setPublishModalOpen(false)}
        onPublish={handlePublish}
        publishing={publishMutation.isPending}
        existingVersion={flow?.latest_version ?? null}
      />

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
            const paramEntries = Object.entries(params);
            if (paramEntries.length === 0) return null;
            return (
              <div key={node.node_id}>
                <Text strong style={{ display: 'block', marginBottom: 8 }}>
                  节点：{node.node_id}（{node.component_name}）
                </Text>
                {paramEntries.map(([key, defaultVal]) => {
                  const formKey = `${node.node_id}__${key}`;
                  const isPath = /path|file/i.test(key);
                  return (
                    <Form.Item
                      key={formKey}
                      name={formKey}
                      label={key}
                      initialValue={defaultVal || ''}
                    >
                      {isPath ? (
                        <Input.Group compact style={{ display: 'flex' }}>
                          <Form.Item name={formKey} noStyle>
                            <Input
                              style={{ flex: 1 }}
                              placeholder={defaultVal ? String(defaultVal) : `输入 ${key}`}
                            />
                          </Form.Item>
                          <Button
                            size="small"
                            onClick={() => setRunFileBrowserOpen(true)}
                          >
                            浏览
                          </Button>
                          <FileBrowserModal
                            open={runFileBrowserOpen}
                            onClose={() => setRunFileBrowserOpen(false)}
                            onSelect={(selectedPath) => {
                              runForm.setFieldValue(formKey, selectedPath);
                              setRunFileBrowserOpen(false);
                            }}
                          />
                        </Input.Group>
                      ) : (
                        <Input
                          placeholder={defaultVal ? String(defaultVal) : `输入 ${key}`}
                        />
                      )}
                    </Form.Item>
                  );
                })}
                <Divider style={{ margin: '12px 0' }} />
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

/** 运行详情面板 */
function RunDetailPanel({
  run,
  nodeColumns,
}: {
  run: FlowRunDetail;
  nodeColumns: ColumnsType<FlowNodeExecution>;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [factModalOpen, setFactModalOpen] = useState(false);
  const [factObjectId, setFactObjectId] = useState<string | undefined>();

  // 从成功的节点中提取 metadata 和全部数据
  const succeededNode = run.nodes.find(
    (n) => n.status === 'succeeded' && n.output_summary,
  );
  const meta = (succeededNode?.output_summary?._metadata ?? {}) as Record<string, unknown>;
  const allRows = (meta.all_rows ?? meta.preview_rows ?? []) as Record<string, unknown>[];
  const header = (meta.header ?? {}) as Record<string, unknown>;
  const exportData = { metadata: header, data: allRows };

  // 查询工业对象
  const { data: objectsData } = useQuery({
    queryKey: ['objects-for-fact'],
    queryFn: () => apiListObjects({ page_size: 100 }),
    enabled: factModalOpen,
  });
  const objectOptions = (objectsData?.items ?? []).map((o) => ({
    value: o.id,
    label: `${o.display_name} (${o.code})`,
  }));

  // 写入事实 Mutation
  const persistFactMutation = useMutation({
    mutationFn: () =>
      apiPersistRunAsFact(run.id, {
        object_id: factObjectId!,
        template_version_id: null,
      }),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['facts'] });
      setFactModalOpen(false);
      message.success(`已写入事实：${data.raw_count} 条观察值（fact_id=${data.fact_id.slice(0, 8)}...）`);
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const canPersistFact = run.status === 'succeeded';

  return (
    <div>
      {canPersistFact && (
        <div style={{ marginBottom: 16 }}>
          <Button type="primary" onClick={() => setFactModalOpen(true)}>
            查看数据 & 写入事实
          </Button>
        </div>
      )}

      <Title level={5}>节点执行（{run.nodes.length} 个）</Title>
      <Table<FlowNodeExecution>
        columns={nodeColumns}
        dataSource={run.nodes}
        rowKey="id"
        pagination={false}
        size="small"
      />
      {run.nodes.length === 0 && (
        <Paragraph type="secondary">暂无节点执行记录</Paragraph>
      )}

      {/* 查看数据 & 写入事实 Modal */}
      <Modal
        title="执行结果数据"
        open={factModalOpen}
        onCancel={() => setFactModalOpen(false)}
        footer={
          <Space>
            <Button onClick={() => setFactModalOpen(false)}>关闭</Button>
            <Button
              type="primary"
              disabled={!factObjectId}
              loading={persistFactMutation.isPending}
              onClick={() => persistFactMutation.mutate()}
            >
              写入事实
            </Button>
          </Space>
        }
        width={720}
      >
        {/* metadata 区域 */}
        <Text strong>Metadata</Text>
        <pre
          style={{
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 6,
            fontSize: 13,
            fontFamily: 'monospace',
            maxHeight: 200,
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            marginTop: 4,
            marginBottom: 16,
          }}
        >
          {JSON.stringify(header, null, 2)}
        </pre>

        {/* 全部数据区域 */}
        <Space style={{ marginBottom: 4, width: '100%', justifyContent: 'space-between' }}>
          <Text strong>数据（{allRows.length} 行）</Text>
          <Button
            size="small"
            onClick={() => {
              const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `run-${run.id.slice(0, 8)}.json`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            导出 JSON
          </Button>
        </Space>
        <pre
          style={{
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 6,
            fontSize: 13,
            fontFamily: 'monospace',
            maxHeight: 400,
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            marginTop: 4,
          }}
        >
          {JSON.stringify(allRows, null, 2)}
        </pre>

        {/* 写入事实区域 */}
        <Divider />
        <Form layout="vertical">
          <Form.Item label="工业对象" required>
            <Select
              placeholder="选择工业对象"
              showSearch
              optionFilterProp="label"
              options={objectOptions}
              value={factObjectId}
              onChange={setFactObjectId}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ============================================================
// 发布版本弹窗 — 可视化节点构建器
// ============================================================

/**
 * 发布流程版本弹窗
 *
 * 提供两种模式：
 * 1. 可视化模式（默认）：通过组件选择器、参数表单、边连线可视化构建流程
 * 2. 高级模式：直接编辑 JSON（保留原始功能作为 fallback）
 */
function PublishVersionModal({
  open,
  onClose,
  onPublish,
  publishing,
  existingVersion,
}: {
  open: boolean;
  onClose: () => void;
  onPublish: (body: {
    nodes: FlowNodeSchema[];
    edges: FlowEdgeSchema[];
    random_seed: number;
  }) => void;
  publishing: boolean;
  existingVersion: {
    id: string;
    version: number;
    nodes?: Record<string, unknown>[];
    edges?: Record<string, unknown>[];
    random_seed?: number;
  } | null;
}): JSX.Element {
  const [advancedMode, setAdvancedMode] = useState(false);
  const [nodes, setNodes] = useState<VisualNode[]>([
    { key: 'initial-node', node_id: 'n1', component_name: '', component_version: '', component_id: '', params: {}, paramsJson: '{}', useJsonParams: false },
  ]);
  const [edges, setEdges] = useState<VisualEdge[]>([]);
  const [randomSeed, setRandomSeed] = useState('');
  const [nodesJson, setNodesJson] = useState('');
  const [edgesJson, setEdgesJson] = useState('');

  // 弹窗打开时重置状态
  useEffect(() => {
    if (open) {
      setAdvancedMode(false);
      setNodes([
        { key: genKey(), node_id: 'n1', component_name: '', component_version: '', component_id: '', params: {}, paramsJson: '{}', useJsonParams: false },
      ]);
      setEdges([]);
      setRandomSeed('');
      setNodesJson('');
      setEdgesJson('');
    }
  }, [open]);

  // ---- 组件列表查询 ----
  const { data: componentsData, isLoading: componentsLoading } = useQuery({
    queryKey: ['components-for-publish'],
    queryFn: () => apiListComponents(),
    enabled: open,
  });

  const allComponents: ComponentSummary[] = componentsData?.items ?? [];
  const modernComponents = allComponents.filter((c) => LLM_COMPONENTS.has(c.name));
  const classicComponents = allComponents.filter((c) => !LLM_COMPONENTS.has(c.name));

  // ---- 组件详情查询（按选中的组件动态获取 manifest）----
  const selectedComponentIds = Array.from(
    new Set(nodes.map((n) => n.component_id).filter(Boolean)),
  );

  const detailQueries = useQueries({
    queries: selectedComponentIds.map((id) => ({
      queryKey: ['component-detail-publish', id] as const,
      queryFn: (): Promise<ComponentDetail> => apiGetComponent(id),
      staleTime: 5 * 60 * 1000,
    })),
  });

  // 构建 componentDetails 映射
  const componentDetails: Record<string, ComponentDetail> = {};
  selectedComponentIds.forEach((id, i) => {
    const query = detailQueries[i];
    if (query?.data) {
      componentDetails[id] = query.data;
    }
  });

  // 构建 parsedManifests 映射
  const parsedManifests: Record<string, ParsedManifest> = {};
  Object.entries(componentDetails).forEach(([id, detail]) => {
    parsedManifests[id] = parseManifest(detail.manifest_yaml);
  });

  // ---- 导入当前设定 ----
  const handleImportCurrent = (): void => {
    if (!existingVersion || !existingVersion.nodes) {
      message.warning('当前流程尚未发布过版本，无可导入的设定');
      return;
    }

    const importedNodes: VisualNode[] = (existingVersion.nodes as FlowNodeSchema[]).map((n) => {
      // 尝试从组件列表中匹配组件 ID
      const comp = allComponents.find((c) => c.name === n.component_name && c.version === n.component_version);
      return {
        key: genKey(),
        node_id: n.node_id,
        component_name: n.component_name,
        component_version: n.component_version,
        component_id: comp?.id ?? '',
        params: (n.params as Record<string, unknown>) ?? {},
        paramsJson: JSON.stringify(n.params ?? {}, null, 2),
        useJsonParams: false,
      };
    });

    const importedEdges: VisualEdge[] = (existingVersion.edges as FlowEdgeSchema[] ?? []).map((e) => ({
      key: genKey(),
      source_node: e.source_node,
      source_port: e.source_port,
      target_node: e.target_node,
      target_port: e.target_port,
    }));

    if (importedNodes.length === 0) {
      message.warning('当前版本无节点定义');
      return;
    }

    setNodes(importedNodes);
    setEdges(importedEdges);
    setRandomSeed(existingVersion.random_seed ? String(existingVersion.random_seed) : '');
    setAdvancedMode(false);
    message.success(`已导入 v${existingVersion.version} 的设定（${importedNodes.length} 个节点，${importedEdges.length} 条边）`);
  };

  // ---- 节点操作 ----
  const addNode = (): void => {
    const existingIds = nodes.map((n) => n.node_id);
    let nextNum = 1;
    while (existingIds.includes(`n${nextNum}`)) {
      nextNum++;
    }
    setNodes([
      ...nodes,
      {
        key: genKey(),
        node_id: `n${nextNum}`,
        component_name: '',
        component_version: '',
        component_id: '',
        params: {},
        paramsJson: '{}',
        useJsonParams: false,
      },
    ]);
  };

  const removeNode = (key: string): void => {
    setNodes(nodes.filter((n) => n.key !== key));
  };

  const updateNode = (key: string, updates: Partial<VisualNode>): void => {
    setNodes(nodes.map((n) => (n.key === key ? { ...n, ...updates } : n)));
  };

  const handleComponentChange = (nodeKey: string, componentId: string): void => {
    const component = allComponents.find((c) => c.id === componentId);
    if (!component) return;
    setNodes(
      nodes.map((n) =>
        n.key === nodeKey
          ? {
              ...n,
              component_id: componentId,
              component_name: component.name,
              component_version: component.version,
              params: {},
              paramsJson: '{}',
              useJsonParams: false,
            }
          : n,
      ),
    );
  };

  // ---- 边操作 ----
  const addEdge = (): void => {
    setEdges([
      ...edges,
      { key: genKey(), source_node: '', source_port: '', target_node: '', target_port: '' },
    ]);
  };

  const removeEdge = (key: string): void => {
    setEdges(edges.filter((e) => e.key !== key));
  };

  const updateEdge = (key: string, updates: Partial<VisualEdge>): void => {
    setEdges(edges.map((e) => (e.key === key ? { ...e, ...updates } : e)));
  };

  // ---- 辅助：获取节点的端口列表 ----
  const nodeIdOptions = nodes.map((n) => ({ value: n.node_id, label: n.node_id }));

  const getNodeOutputPorts = (nodeId: string): PortDef[] => {
    const node = nodes.find((n) => n.node_id === nodeId);
    if (!node || !node.component_id) return [];
    const manifest = parsedManifests[node.component_id];
    if (!manifest) return [];
    return manifest.outputs;
  };

  const getNodeInputPorts = (nodeId: string): PortDef[] => {
    const node = nodes.find((n) => n.node_id === nodeId);
    if (!node || !node.component_id) return [];
    const manifest = parsedManifests[node.component_id];
    if (!manifest) return [];
    return manifest.inputs;
  };

  // ---- 发布处理 ----
  const handlePublish = (): void => {
    if (advancedMode) {
      // 高级模式：解析 JSON
      let parsedNodes: FlowNodeSchema[];
      let parsedEdges: FlowEdgeSchema[];
      try {
        parsedNodes = JSON.parse(nodesJson) as FlowNodeSchema[];
        parsedEdges = edgesJson ? (JSON.parse(edgesJson) as FlowEdgeSchema[]) : [];
      } catch (err) {
        message.error(
          `JSON 解析失败: ${err instanceof Error ? err.message : String(err)}`,
        );
        return;
      }
      onPublish({
        nodes: parsedNodes,
        edges: parsedEdges,
        random_seed: 0,
      });
      return;
    }

    // 可视化模式：验证 & 转换
    if (nodes.length === 0) {
      message.error('请至少添加一个节点');
      return;
    }
    const emptyNodes = nodes.filter((n) => !n.component_name);
    if (emptyNodes.length > 0) {
      message.error('存在未选择组件的节点，请删除或补全');
      return;
    }

    const flowNodes: FlowNodeSchema[] = [];
    for (const n of nodes) {
      let params: Record<string, unknown> = {};

      if (n.useJsonParams) {
        try {
          params = n.paramsJson ? JSON.parse(n.paramsJson) : {};
        } catch (err) {
          message.error(
            `节点 ${n.node_id} 的参数 JSON 解析失败: ${err instanceof Error ? err.message : String(err)}`,
          );
          return;
        }
      } else {
        params = { ...n.params };
        // 从 manifest 填充默认值
        const manifest = n.component_id ? parsedManifests[n.component_id] : undefined;
        if (manifest) {
          for (const param of manifest.params) {
            if (!(param.name in params)) {
              // 留空的参数也存入 params（用空值），这样创建执行时弹窗能显示输入框
              params[param.name] = param.default !== undefined ? param.default : '';
            }
            // 复杂类型的字符串值尝试 JSON 解析
            if (
              isComplexType(param.type) &&
              typeof params[param.name] === 'string'
            ) {
              try {
                params[param.name] = JSON.parse(params[param.name] as string);
              } catch {
                // 保留字符串
              }
            }
          }
        }
      }

      flowNodes.push({
        node_id: n.node_id,
        component_name: n.component_name,
        component_version: n.component_version,
        params,
      });
    }

    const flowEdges: FlowEdgeSchema[] = edges
      .filter((e) => e.source_node && e.target_node)
      .map((e) => ({
        source_node: e.source_node,
        source_port: e.source_port,
        target_node: e.target_node,
        target_port: e.target_port,
      }));

    onPublish({
      nodes: flowNodes,
      edges: flowEdges,
      random_seed: Number(randomSeed) || 0,
    });
  };

  return (
    <Modal
      title="发布流程版本"
      open={open}
      onOk={handlePublish}
      onCancel={onClose}
      confirmLoading={publishing}
      okText="发布"
      cancelText="取消"
      width={780}
    >
      {/* 顶部操作栏：导入当前设定 + 高级模式切换 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Button
          size="small"
          onClick={handleImportCurrent}
          disabled={!existingVersion?.nodes}
        >
          导入当前设定
          {existingVersion?.version && (
            <Tag color="blue" style={{ marginLeft: 6, fontSize: 10 }}>
              v{existingVersion.version}
            </Tag>
          )}
        </Button>
        <Space>
          <Text type="secondary">高级模式（手动 JSON）</Text>
          <Switch checked={advancedMode} onChange={setAdvancedMode} />
        </Space>
      </div>

      {advancedMode ? (
        /* ---- 高级模式：JSON 编辑器 ---- */
        <Form layout="vertical">
          <Form.Item label="节点定义 (JSON)" required>
            <Input.TextArea
              rows={8}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder={`[\n  {"node_id":"n1","component_name":"csv_reader","component_version":"1.0.0","params":{},"input_bindings":{}}\n]`}
              value={nodesJson}
              onChange={(e) => setNodesJson(e.target.value)}
            />
          </Form.Item>
          <Form.Item label="边定义 (JSON，可选)">
            <Input.TextArea
              rows={4}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder={`[\n  {"source_node":"n1","source_port":"out","target_node":"n2","target_port":"in"}\n]`}
              value={edgesJson}
              onChange={(e) => setEdgesJson(e.target.value)}
            />
          </Form.Item>
        </Form>
      ) : (
        /* ---- 可视化模式 ---- */
        <div>
          {/* 节点列表 */}
          <div
            style={{
              marginBottom: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <Title level={5} style={{ margin: 0 }}>
              节点定义
            </Title>
            <Button type="dashed" size="small" onClick={addNode}>
              + 添加节点
            </Button>
          </div>

          {componentsLoading && (
            <div style={{ textAlign: 'center', padding: 16 }}>
              <Spin tip="加载组件列表..." />
            </div>
          )}

          {!componentsLoading &&
            nodes.map((node) => (
              <NodeEditorCard
                key={node.key}
                node={node}
                modernComponents={modernComponents}
                classicComponents={classicComponents}
                componentsLoading={componentsLoading}
                componentDetail={
                  node.component_id ? componentDetails[node.component_id] : undefined
                }
                parsedManifest={
                  node.component_id ? parsedManifests[node.component_id] : undefined
                }
                onComponentChange={(componentId) =>
                  handleComponentChange(node.key, componentId)
                }
                onUpdate={(updates) => updateNode(node.key, updates)}
                onRemove={() => removeNode(node.key)}
              />
            ))}

          {/* 边定义 */}
          <Divider orientation="left" plain>
            边定义（连线）
          </Divider>

          <div
            style={{
              marginBottom: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <Text type="secondary" style={{ fontSize: 12 }}>
              连接节点的输出端口到下游节点的输入端口
            </Text>
            <Button type="dashed" size="small" onClick={addEdge}>
              + 添加边
            </Button>
          </div>

          {edges.length > 0 ? (
            edges.map((edge) => (
              <EdgeEditorRow
                key={edge.key}
                edge={edge}
                nodeIdOptions={nodeIdOptions}
                getOutputPorts={getNodeOutputPorts}
                getInputPorts={getNodeInputPorts}
                onUpdate={(updates) => updateEdge(edge.key, updates)}
                onRemove={() => removeEdge(edge.key)}
              />
            ))
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              暂无连线，单节点流程可不添加
            </Text>
          )}

          <Text type="secondary" style={{ fontSize: 11 }}>
            提示：如需设置 input_bindings，请切换到高级模式
          </Text>
        </div>
      )}
    </Modal>
  );
}

// ============================================================
// 节点编辑卡片
// ============================================================

/**
 * 单个节点的可视化编辑卡片
 *
 * 包含：节点 ID 输入、组件选择器（摩登/古法分组）、参数编辑区
 */
function NodeEditorCard({
  node,
  modernComponents,
  classicComponents,
  componentsLoading,
  componentDetail,
  parsedManifest,
  onComponentChange,
  onUpdate,
  onRemove,
}: {
  node: VisualNode;
  modernComponents: ComponentSummary[];
  classicComponents: ComponentSummary[];
  componentsLoading: boolean;
  componentDetail?: ComponentDetail;
  parsedManifest?: ParsedManifest;
  onComponentChange: (componentId: string) => void;
  onUpdate: (updates: Partial<VisualNode>) => void;
  onRemove: () => void;
}): JSX.Element {
  const hasParsedParams = !!(parsedManifest && parsedManifest.params.length > 0);
  const componentLoading = !!node.component_id && !componentDetail;

  return (
    <div
      style={{
        border: '1px solid #e8e8e8',
        borderRadius: 6,
        padding: 12,
        marginBottom: 12,
        background: '#fafafa',
      }}
    >
      {/* 行 1：节点 ID + 组件选择 + 删除 */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        <Input
          style={{ width: 80 }}
          value={node.node_id}
          onChange={(e) => onUpdate({ node_id: e.target.value })}
          placeholder="n1"
        />
        <Select
          showSearch
          optionFilterProp="label"
          style={{ flex: 1 }}
          placeholder="选择组件"
          value={node.component_id || undefined}
          onChange={(value: string) => onComponentChange(value)}
          loading={componentsLoading}
          allowClear
        >
          {modernComponents.length > 0 && (
            <Select.OptGroup
              key="modern"
              label={
                <span>
                  <Tag color="purple" style={{ marginRight: 4, fontSize: 10 }}>
                    AI
                  </Tag>
                  摩登
                </span>
              }
            >
              {modernComponents.map((c) => (
                <Select.Option
                  key={c.id}
                  value={c.id}
                  label={`${c.name} v${c.version}`}
                >
                  <Space size={4}>
                    <Tag color="purple" style={{ margin: 0, fontSize: 10 }}>
                      AI
                    </Tag>
                    <span>{c.name}</span>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      v{c.version}
                    </Text>
                    {KIND_LABEL[c.kind] && (
                      <Tag style={{ fontSize: 10, margin: 0 }}>
                        {KIND_LABEL[c.kind]}
                      </Tag>
                    )}
                  </Space>
                </Select.Option>
              ))}
            </Select.OptGroup>
          )}
          {classicComponents.length > 0 && (
            <Select.OptGroup
              key="classic"
              label={
                <span>
                  <Tag color="blue" style={{ marginRight: 4, fontSize: 10 }}>
                    Code
                  </Tag>
                  古法
                </span>
              }
            >
              {classicComponents.map((c) => (
                <Select.Option
                  key={c.id}
                  value={c.id}
                  label={`${c.name} v${c.version}`}
                >
                  <Space size={4}>
                    <Tag color="blue" style={{ margin: 0, fontSize: 10 }}>
                      Code
                    </Tag>
                    <span>{c.name}</span>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      v{c.version}
                    </Text>
                    {KIND_LABEL[c.kind] && (
                      <Tag style={{ fontSize: 10, margin: 0 }}>
                        {KIND_LABEL[c.kind]}
                      </Tag>
                    )}
                  </Space>
                </Select.Option>
              ))}
            </Select.OptGroup>
          )}
        </Select>
        <Button danger size="small" onClick={onRemove}>
          删除
        </Button>
      </div>

      {/* 行 2：版本显示 */}
      {node.component_name && (
        <div style={{ marginBottom: 8, paddingLeft: 4 }}>
          <Space size={8}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              组件: <Text strong>{node.component_name}</Text>
            </Text>
            <Tag color="green">v{node.component_version}</Tag>
          </Space>
        </div>
      )}

      {/* 行 3：参数编辑区 */}
      {componentLoading ? (
        <div style={{ paddingLeft: 4, padding: '4px 0' }}>
          <Spin size="small" />{' '}
          <Text type="secondary" style={{ fontSize: 12 }}>
            加载组件清单...
          </Text>
        </div>
      ) : node.useJsonParams ? (
        /* JSON 参数编辑模式 */
        <div style={{ paddingLeft: 4 }}>
          <Input.TextArea
            rows={3}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
            value={node.paramsJson}
            onChange={(e) => onUpdate({ paramsJson: e.target.value })}
            placeholder={`{\n  "key": "value"\n}`}
          />
          {hasParsedParams && (
            <Button
              type="link"
              size="small"
              onClick={() => onUpdate({ useJsonParams: false })}
            >
              切换到表单编辑
            </Button>
          )}
        </div>
      ) : hasParsedParams ? (
        /* 表单参数编辑模式 */
        <div style={{ paddingLeft: 4 }}>
          {parsedManifest!.params.map((param) => (
            <ParamInputRow
              key={param.name}
              param={param}
              value={node.params[param.name]}
              onChange={(newValue) =>
                onUpdate({
                  params: { ...node.params, [param.name]: newValue },
                })
              }
            />
          ))}
          <Button
            type="link"
            size="small"
            onClick={() =>
              onUpdate({
                useJsonParams: true,
                paramsJson: JSON.stringify(node.params, null, 2) || '{}',
              })
            }
          >
            切换到 JSON 编辑
          </Button>
        </div>
      ) : node.component_id ? (
        /* 组件已选中但无法解析参数 — JSON fallback */
        <div style={{ paddingLeft: 4 }}>
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
            该组件无参数定义（或无法解析），可手动输入 JSON 参数（可选）
          </Text>
          <Input.TextArea
            rows={3}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
            value={node.paramsJson}
            onChange={(e) => onUpdate({ paramsJson: e.target.value })}
            placeholder={`{\n  "key": "value"\n}`}
          />
        </div>
      ) : null}
    </div>
  );
}

// ============================================================
// 参数输入行
// ============================================================

/**
 * 单个参数的输入行
 *
 * 根据参数类型自动选择合适的输入控件：
 * - boolean → Select (true/false)
 * - array/object → TextArea (JSON)
 * - string 且参数名含 path/file → Input + 浏览按钮（打开文件浏览器）
 * - 其他 → Input
 */
function ParamInputRow({
  param,
  value,
  onChange,
}: {
  param: ParamDef;
  value: unknown;
  onChange: (newValue: unknown) => void;
}): JSX.Element {
  const t = param.type.toLowerCase();
  const [fileBrowserOpen, setFileBrowserOpen] = useState(false);

  // 判断是否需要文件浏览器（参数名含 path/file 且为 string 类型）
  const needsFileBrowser =
    (t === 'string') &&
    /path|file/i.test(param.name);

  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ marginBottom: 2 }}>
        <Space size={4}>
          <Text strong style={{ fontSize: 13 }}>
            {param.name}
          </Text>
          {param.required && (
            <Text type="danger" style={{ fontSize: 11 }}>
              必填
            </Text>
          )}
          <Tag style={{ fontSize: 10, margin: 0 }}>{param.type}</Tag>
        </Space>
        {param.description && (
          <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
            {param.description}
          </Text>
        )}
      </div>

      {t === 'boolean' || t === 'bool' ? (
        <Select
          size="small"
          style={{ width: '100%' }}
          value={value !== undefined ? String(value) : undefined}
          onChange={(v: string) => onChange(v === 'true')}
          options={[
            { value: 'true', label: 'true' },
            { value: 'false', label: 'false' },
          ]}
          placeholder="选择..."
          allowClear
        />
      ) : isComplexType(param.type) ? (
        <Input.TextArea
          rows={2}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
          value={typeof value === 'string' ? value : JSON.stringify(value ?? param.default ?? '', null, 2)}
          onChange={(e) => onChange(e.target.value)}
          placeholder={
            param.default !== undefined
              ? JSON.stringify(param.default, null, 2)
              : `输入 ${param.type} 格式的 JSON`
          }
        />
      ) : needsFileBrowser ? (
        /* 文件路径参数：输入框 + 浏览按钮 */
        <>
        <div style={{ display: 'flex', gap: 4 }}>
          <Input
            size="small"
            style={{ flex: 1 }}
            value={String(value ?? param.default ?? '')}
            onChange={(e) => {
              const newValue = convertParamValue(e.target.value, param.type);
              onChange(newValue);
            }}
            placeholder={
              param.default !== undefined
                ? String(param.default)
                : `请输入 ${param.name}`
            }
          />
          <Button
            size="small"
            onClick={() => setFileBrowserOpen(true)}
          >
            浏览
          </Button>
        </div>
        <FileBrowserModal
          open={fileBrowserOpen}
          onClose={() => setFileBrowserOpen(false)}
          onSelect={(selectedPath) => {
            onChange(convertParamValue(selectedPath, param.type));
            setFileBrowserOpen(false);
          }}
        />
        </>
      ) : (
        <Input
          size="small"
          value={String(value ?? param.default ?? '')}
          onChange={(e) => {
            const newValue = convertParamValue(e.target.value, param.type);
            onChange(newValue);
          }}
          placeholder={
            param.default !== undefined
              ? String(param.default)
              : `请输入 ${param.name}`
          }
        />
      )}
    </div>
  );
}

// ============================================================
// 文件浏览器弹窗
// ============================================================

/**
 * 文件浏览器弹窗
 *
 * 调用后端 /files/browse API 列出服务器端目录内容，
 * 支持目录导航和文件选择。
 */
function FileBrowserModal({
  open,
  onClose,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  onSelect: (path: string) => void;
}): JSX.Element {
  const [currentPath, setCurrentPath] = useState('');
  const [browseData, setBrowseData] = useState<BrowseResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const loadDir = async (path: string): Promise<void> => {
    setLoading(true);
    try {
      const data = await apiBrowseFiles(path || undefined);
      setBrowseData(data);
      setCurrentPath(data.current_path);
    } catch (err) {
      message.error(`浏览失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      void loadDir('');
    }
  }, [open]);

  const handleDirClick = (dirName: string): void => {
    const newPath = currentPath ? `${currentPath}/${dirName}` : dirName;
    void loadDir(newPath);
  };

  const handleParentClick = (): void => {
    if (browseData?.parent_path !== null && browseData?.parent_path !== undefined) {
      void loadDir(browseData.parent_path);
    }
  };

  // 选中文件时拼接完整路径（相对于浏览根目录的绝对路径）
  const handleFileSelect = (fileName: string): void => {
    const relPath = currentPath ? `${currentPath}/${fileName}` : fileName;
    // 拼接为服务器绝对路径
    onSelect(`/Users/shuipei/Desktop/snowSP/irip/${relPath}`);
  };

  return (
    <Modal
      title="选择文件"
      open={open}
      onCancel={onClose}
      footer={null}
      width={560}
    >
      {/* 当前路径 + 上级按钮 */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Button
          size="small"
          disabled={!browseData?.parent_path && currentPath === ''}
          onClick={handleParentClick}
        >
          上级
        </Button>
        <Text type="secondary" style={{ fontSize: 12, flex: 1, fontFamily: 'monospace' }}>
          /{currentPath || ''}
        </Text>
      </div>

      {/* 文件列表 */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin />
        </div>
      ) : (
        <div style={{ maxHeight: 360, overflow: 'auto', border: '1px solid #e8e8e8', borderRadius: 4 }}>
          {browseData?.items.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Text type="secondary">空目录</Text>
            </div>
          ) : (
            browseData?.items.map((item) => (
              <div
                key={item.name}
                style={{
                  padding: '6px 12px',
                  cursor: 'pointer',
                  borderBottom: '1px solid #f0f0f0',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = '#f5f5f5';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent';
                }}
                onClick={() => {
                  if (item.type === 'dir') {
                    handleDirClick(item.name);
                  } else {
                    handleFileSelect(item.name);
                  }
                }}
              >
                <Tag color={item.type === 'dir' ? 'blue' : 'default'} style={{ margin: 0, fontSize: 10 }}>
                  {item.type === 'dir' ? 'DIR' : 'FILE'}
                </Tag>
                <Text style={{ fontSize: 13 }}>{item.name}</Text>
                {item.size !== null && item.type === 'file' && (
                  <Text type="secondary" style={{ fontSize: 11, marginLeft: 'auto' }}>
                    {item.size > 1024 ? `${(item.size / 1024).toFixed(1)} KB` : `${item.size} B`}
                  </Text>
                )}
              </div>
            ))
          )}
        </div>
      )}

      <Text type="secondary" style={{ fontSize: 11, marginTop: 8, display: 'block' }}>
        点击目录进入，点击文件选择
      </Text>
    </Modal>
  );
}

// ============================================================
// 边编辑行
// ============================================================

/**
 * 单条边的可视化编辑行
 *
 * 布局：源节点 → 源端口 → 目标节点 → 目标端口 → 删除
 */
function EdgeEditorRow({
  edge,
  nodeIdOptions,
  getOutputPorts,
  getInputPorts,
  onUpdate,
  onRemove,
}: {
  edge: VisualEdge;
  nodeIdOptions: { value: string; label: string }[];
  getOutputPorts: (nodeId: string) => PortDef[];
  getInputPorts: (nodeId: string) => PortDef[];
  onUpdate: (updates: Partial<VisualEdge>) => void;
  onRemove: () => void;
}): JSX.Element {
  const sourcePorts = getOutputPorts(edge.source_node);
  const targetPorts = getInputPorts(edge.target_node);

  return (
    <div
      style={{
        display: 'flex',
        gap: 4,
        alignItems: 'center',
        marginBottom: 8,
        flexWrap: 'wrap',
      }}
    >
      <Select
        style={{ width: 90 }}
        placeholder="源节点"
        value={edge.source_node || undefined}
        onChange={(value: string) =>
          onUpdate({ source_node: value, source_port: '' })
        }
        options={nodeIdOptions}
        allowClear
      />
      <Text type="secondary">→</Text>
      {sourcePorts.length > 0 ? (
        <Select
          style={{ width: 110 }}
          placeholder="源端口"
          value={edge.source_port || undefined}
          onChange={(value: string) => onUpdate({ source_port: value })}
          options={sourcePorts.map((p) => ({ value: p.name, label: p.name }))}
          allowClear
        />
      ) : (
        <Input
          style={{ width: 110 }}
          placeholder="端口名"
          value={edge.source_port}
          onChange={(e) => onUpdate({ source_port: e.target.value })}
        />
      )}
      <Text type="secondary">→</Text>
      <Select
        style={{ width: 90 }}
        placeholder="目标节点"
        value={edge.target_node || undefined}
        onChange={(value: string) =>
          onUpdate({ target_node: value, target_port: '' })
        }
        options={nodeIdOptions}
        allowClear
      />
      <Text type="secondary">→</Text>
      {targetPorts.length > 0 ? (
        <Select
          style={{ width: 110 }}
          placeholder="目标端口"
          value={edge.target_port || undefined}
          onChange={(value: string) => onUpdate({ target_port: value })}
          options={targetPorts.map((p) => ({ value: p.name, label: p.name }))}
          allowClear
        />
      ) : (
        <Input
          style={{ width: 110 }}
          placeholder="端口名"
          value={edge.target_port}
          onChange={(e) => onUpdate({ target_port: e.target.value })}
        />
      )}
      <Button danger size="small" onClick={onRemove}>
        删除
      </Button>
    </div>
  );
}

export default FlowDetail;
