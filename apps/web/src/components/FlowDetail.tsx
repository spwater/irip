import { useState } from 'react';
import {
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCancelFlowRun,
  apiCreateFlow,
  apiCreateFlowRun,
  apiGetFlow,
  apiGetFlowRun,
  apiListFlows,
  apiPublishFlow,
  apiResumeFlowRun,
  apiRetryFlowNode,
  extractApiError,
  type FlowNodeExecution,
  type FlowRunDetail,
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

/**
 * 流程编排页面（IRIP V2-T05）
 *
 * 功能：
 * - 流程列表 Table（编码 / 名称 / 状态 / 最新版本）
 * - 顶部「新建流程」按钮 → Modal（编码 + 名称）
 * - 选中流程 → 展示基本信息 + 运行操作（执行 / 恢复 / 取消）
 * - 节点执行列表 Table（节点 ID / 状态 / 耗时）
 * - 发布版本 Modal（nodes/edges JSON + random_seed）
 */
export function FlowDetail(): JSX.Element {
  const queryClient = useQueryClient();
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [publishModalOpen, setPublishModalOpen] = useState(false);
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [runIdInput, setRunIdInput] = useState<string>('');
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [createForm] = Form.useForm();
  const [publishForm] = Form.useForm();
  const [runForm] = Form.useForm();

  // ---- 流程列表查询 ----
  const { data: listData, isLoading: listLoading } = useQuery({
    queryKey: ['flows'],
    queryFn: () => apiListFlows(),
  });

  const flows: FlowSummary[] = listData?.items ?? [];

  // ---- 选中流程详情查询 ----
  const { data: flow, isLoading: flowLoading } = useQuery({
    queryKey: ['flow', selectedFlowId],
    queryFn: () => apiGetFlow(selectedFlowId!),
    enabled: !!selectedFlowId,
  });

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
      publishForm.resetFields();
      message.success('流程版本发布成功');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 创建运行 Mutation ----
  const createRunMutation = useMutation({
    mutationFn: (vars: { flowId: string; body: { inputs: Record<string, unknown> } }) =>
      apiCreateFlowRun(vars.flowId, vars.body),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['flow-run', data.id] });
      setRunModalOpen(false);
      runForm.resetFields();
      message.success('流程执行已创建');
      setActiveRunId(data.id);
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 恢复 / 取消 Mutation ----
  const resumeMutation = useMutation({
    mutationFn: apiResumeFlowRun,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-run', activeRunId] });
      message.success('流程执行已恢复');
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

  const handlePublish = async (): Promise<void> => {
    if (!selectedFlowId) return;
    try {
      const values = await publishForm.validateFields();
      const nodes = JSON.parse(values.nodes_json as string);
      const edges = values.edges_json
        ? JSON.parse(values.edges_json as string)
        : [];
      publishMutation.mutate({
        flowId: selectedFlowId,
        body: {
          nodes,
          edges,
          random_seed: Number(values.random_seed ?? 0),
        },
      });
    } catch (err) {
      message.error(`JSON 解析失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleCreateRun = async (): Promise<void> => {
    if (!selectedFlowId) return;
    try {
      const values = await runForm.validateFields();
      const inputs = values.inputs_json
        ? JSON.parse(values.inputs_json as string)
        : {};
      createRunMutation.mutate({ flowId: selectedFlowId, body: { inputs } });
    } catch (err) {
      message.error(`JSON 解析失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleLoadRun = (): void => {
    if (runIdInput.trim()) {
      setActiveRunId(runIdInput.trim());
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
    { title: '节点 ID', dataIndex: 'node_id', key: 'node_id', width: 180 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => (
        <Tag color={NODE_STATUS_COLOR[v] ?? 'default'}>
          {NODE_STATUS_LABEL[v] ?? v}
        </Tag>
      ),
    },
    {
      title: '耗时 (ms)',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 120,
      render: (v: number | null) => (v != null ? v : '-'),
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      key: 'started_at',
      width: 200,
      render: (v: string | null) => v ?? '-',
    },
    {
      title: '完成时间',
      dataIndex: 'completed_at',
      key: 'completed_at',
      width: 200,
      render: (v: string | null) => v ?? '-',
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

      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={() => setCreateModalOpen(true)}>
          新建流程
        </Button>
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
                <Descriptions.Item label="状态">
                  <Tag color={STATUS_COLOR[flow.status] ?? 'default'}>
                    {STATUS_LABEL[flow.status] ?? flow.status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="锁版本">{flow.lock_version}</Descriptions.Item>
                <Descriptions.Item label="最新版本">
                  {flow.latest_version
                    ? `v${flow.latest_version.version} (${flow.latest_version.digest.slice(0, 12)}…)`
                    : '未发布'}
                </Descriptions.Item>
                <Descriptions.Item label="创建时间">{flow.created_at}</Descriptions.Item>
              </Descriptions>

              <Space style={{ marginTop: 16 }} wrap>
                <Button
                  onClick={() => {
                    publishForm.resetFields();
                    setPublishModalOpen(true);
                  }}
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
              </Space>
            </>
          )}
        </Card>
      )}

      {/* 运行管理 */}
      {selectedFlowId && (
        <Card title="运行管理" style={{ marginBottom: 16 }}>
          <Space style={{ marginBottom: 16 }}>
            <Input
              placeholder="输入运行 ID"
              style={{ width: 360 }}
              value={runIdInput}
              onChange={(e) => setRunIdInput(e.target.value)}
            />
            <Button type="primary" onClick={handleLoadRun}>
              加载运行
            </Button>
            {activeRunId && (
              <>
                <Popconfirm
                  title="确认恢复执行？"
                  onConfirm={() => resumeMutation.mutate(activeRunId)}
                  okText="确定"
                  cancelText="取消"
                >
                  <Button>恢复</Button>
                </Popconfirm>
                <Popconfirm
                  title="确认取消执行？"
                  onConfirm={() => cancelMutation.mutate(activeRunId)}
                  okText="确定"
                  cancelText="取消"
                >
                  <Button danger>取消</Button>
                </Popconfirm>
              </>
            )}
          </Space>

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
            <Empty description="请输入运行 ID 加载执行详情" />
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

      {/* 发布版本 Modal */}
      <Modal
        title="发布流程版本"
        open={publishModalOpen}
        onOk={handlePublish}
        onCancel={() => {
          setPublishModalOpen(false);
          publishForm.resetFields();
        }}
        confirmLoading={publishMutation.isPending}
        okText="发布"
        cancelText="取消"
        width={680}
      >
        <Form form={publishForm} layout="vertical">
          <Form.Item
            name="nodes_json"
            label="节点定义 (JSON)"
            rules={[{ required: true, message: '请输入节点定义 JSON' }]}
          >
            <Input.TextArea
              rows={8}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder={`[\n  {"node_id":"n1","component_name":"csv_reader","component_version":"1.0.0","params":{},"input_bindings":{}}\n]`}
            />
          </Form.Item>
          <Form.Item name="edges_json" label="边定义 (JSON，可选)">
            <Input.TextArea
              rows={4}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder={`[\n  {"source_node":"n1","source_port":"out","target_node":"n2","target_port":"in"}\n]`}
            />
          </Form.Item>
          <Form.Item name="random_seed" label="随机种子">
            <Input placeholder="0" />
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
          <Form.Item name="inputs_json" label="输入参数 (JSON，可选)">
            <Input.TextArea
              rows={6}
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
  return (
    <div>
      <Descriptions bordered column={2} size="small" style={{ marginBottom: 16 }}>
        <Descriptions.Item label="运行 ID">
          <Text copyable style={{ fontFamily: 'monospace', fontSize: 12 }}>
            {run.id}
          </Text>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={RUN_STATUS_COLOR[run.status] ?? 'default'}>
            {RUN_STATUS_LABEL[run.status] ?? run.status}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="流程版本 ID">
          <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>
            {run.flow_version_id.slice(0, 12)}…
          </Text>
        </Descriptions.Item>
        <Descriptions.Item label="作业 ID">
          {run.job_id ?? '-'}
        </Descriptions.Item>
        <Descriptions.Item label="输出摘要">
          {run.output_digest ? (
            <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>
              {run.output_digest.slice(0, 16)}…
            </Text>
          ) : (
            '-'
          )}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">{run.created_at}</Descriptions.Item>
        <Descriptions.Item label="开始时间">{run.started_at ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="完成时间">{run.completed_at ?? '-'}</Descriptions.Item>
      </Descriptions>

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
    </div>
  );
}

export default FlowDetail;
