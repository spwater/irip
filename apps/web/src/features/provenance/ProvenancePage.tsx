import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tabs,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateDerivationRun,
  apiCreateEvidenceSet,
  apiCreateRecipe,
  apiFreezeEvidenceSet,
  apiGetProvenanceGraph,
  apiListDerivationRuns,
  apiListEvidenceSets,
  apiListRecipes,
  apiPublishRecipe,
  apiReplayDerivation,
} from '@/api/facts-provenance';
import { extractApiError, type DerivationRun, type EvidenceSet, type ProvenanceEdge, type ProvenanceNode, type Recipe } from '@/api/types';

const { Title, Text } = Typography;

/** 状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  draft: 'blue',
  in_review: 'orange',
  published: 'green',
  frozen: 'cyan',
  active: 'green',
  succeeded: 'green',
  failed: 'red',
  running: 'processing',
  deprecated: 'default',
  pending: 'default',
};

/** 状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  in_review: '审核中',
  published: '已发布',
  frozen: '已冻结',
  active: '活跃',
  succeeded: '成功',
  failed: '失败',
  running: '运行中',
  deprecated: '已弃用',
  pending: '待处理',
};

/** 节点类型 → 中文标签 */
const NODE_TYPE_LABEL: Record<string, string> = {
  fact_revision: '事实版本',
  observation: '观测',
  intermediate_artifact: '中间产物',
  derivation_run: '推导运行',
  parameter_version: '参数版本',
};

/**
 * 溯源链路页面
 *
 * 功能：
 * - Tabs: 证据集 / 配方 / 推导运行 / 溯源图谱
 * - 证据集：列表 + 创建 + 冻结
 * - 配方：列表 + 创建 + 发布
 * - 推导运行：列表 + 创建 + 重放 + 查看图谱
 * - 溯源图谱：选择运行后展示节点和边的表格
 *
 * @param initialRunId - 可选，从深链传入的推导运行 ID。若提供则自动切换到图谱 Tab 并选中该运行。
 */
export function ProvenancePage({ initialRunId }: { initialRunId?: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState(initialRunId ? 'graph' : 'evidence-sets');

  // 证据集状态
  const [evidenceModalOpen, setEvidenceModalOpen] = useState(false);
  const [evidenceForm] = Form.useForm();

  // 配方状态
  const [recipeModalOpen, setRecipeModalOpen] = useState(false);
  const [recipeForm] = Form.useForm();

  // 推导运行状态
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [runForm] = Form.useForm();

  // 图谱状态
  const [selectedRunId, setSelectedRunId] = useState<string | undefined>(initialRunId);

  // 当 initialRunId 变化时（同页面深链导航），同步选中运行和图谱 Tab
  useEffect(() => {
    if (initialRunId) {
      setSelectedRunId(initialRunId);
      setActiveTab('graph');
    }
  }, [initialRunId]);

  // ---- 数据查询 ----
  const { data: evidenceSetsData, isLoading: evidenceLoading } = useQuery({
    queryKey: ['evidence-sets'],
    queryFn: () => apiListEvidenceSets({ page_size: 50 }),
  });
  const evidenceSets: EvidenceSet[] = evidenceSetsData?.items ?? [];

  const { data: recipesData, isLoading: recipesLoading } = useQuery({
    queryKey: ['recipes'],
    queryFn: () => apiListRecipes({ page_size: 50 }),
  });
  const recipes: Recipe[] = recipesData?.items ?? [];

  const { data: runsData, isLoading: runsLoading } = useQuery({
    queryKey: ['derivation-runs'],
    queryFn: () => apiListDerivationRuns({ page_size: 50 }),
  });
  const runs: DerivationRun[] = runsData?.items ?? [];

  const { data: graph } = useQuery({
    queryKey: ['provenance-graph', selectedRunId],
    queryFn: () => apiGetProvenanceGraph(selectedRunId!),
    enabled: !!selectedRunId,
  });

  // ---- Mutations ----
  const createEvidenceSetMutation = useMutation({
    mutationFn: apiCreateEvidenceSet,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evidence-sets'] });
      setEvidenceModalOpen(false);
      evidenceForm.resetFields();
      message.success('证据集创建成功');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const freezeEvidenceSetMutation = useMutation({
    mutationFn: apiFreezeEvidenceSet,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['evidence-sets'] });
      message.success('证据集已冻结');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const createRecipeMutation = useMutation({
    mutationFn: apiCreateRecipe,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['recipes'] });
      setRecipeModalOpen(false);
      recipeForm.resetFields();
      message.success('配方创建成功');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const publishRecipeMutation = useMutation({
    mutationFn: (recipeId: string) => apiPublishRecipe(recipeId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['recipes'] });
      message.success('配方已发布');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const createRunMutation = useMutation({
    mutationFn: apiCreateDerivationRun,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['derivation-runs'] });
      setRunModalOpen(false);
      runForm.resetFields();
      message.success('推导运行创建成功');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const replayRunMutation = useMutation({
    mutationFn: (runId: string) => apiReplayDerivation(runId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['derivation-runs'] });
      message.success('推导重放已启动');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 表单提交处理 ----
  const handleEvidenceSubmit = async (): Promise<void> => {
    try {
      const values = await evidenceForm.validateFields();
      createEvidenceSetMutation.mutate({ name: values.name });
    } catch {
      // 校验失败
    }
  };

  const handleRecipeSubmit = async (): Promise<void> => {
    try {
      const values = await recipeForm.validateFields();
      createRecipeMutation.mutate({
        code: values.code,
        display_name: values.display_name,
      });
    } catch {
      // 校验失败
    }
  };

  const handleRunSubmit = async (): Promise<void> => {
    try {
      const values = await runForm.validateFields();
      createRunMutation.mutate({
        evidence_set_version_id: values.evidence_set_version_id,
        recipe_version_id: values.recipe_version_id,
      });
    } catch {
      // 校验失败
    }
  };

  // ---- 表格列定义 ----
  const evidenceSetColumns: ColumnsType<EvidenceSet> = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] ?? 'default'}>{STATUS_LABEL[s] ?? s}</Tag>
      ),
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 80,
      align: 'center' as const,
    },
    {
      title: '成员数',
      dataIndex: 'member_count',
      key: 'member_count',
      width: 80,
      align: 'center' as const,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: EvidenceSet) =>
        record.status !== 'frozen' && (
          <Button
            type="link"
            size="small"
            onClick={() => freezeEvidenceSetMutation.mutate(record.set_id)}
          >
            冻结
          </Button>
        ),
    },
  ];

  const recipeColumns: ColumnsType<Recipe> = [
    { title: '编码', dataIndex: 'code', key: 'code', width: 160 },
    { title: '名称', dataIndex: 'display_name', key: 'display_name' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] ?? 'default'}>{STATUS_LABEL[s] ?? s}</Tag>
      ),
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 80,
      align: 'center' as const,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: Recipe) =>
        record.status !== 'published' && (
          <Button
            type="link"
            size="small"
            onClick={() => publishRecipeMutation.mutate(record.recipe_id)}
          >
            发布
          </Button>
        ),
    },
  ];

  const runColumns: ColumnsType<DerivationRun> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 280 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] ?? 'default'}>{STATUS_LABEL[s] ?? s}</Tag>
      ),
    },
    {
      title: '输出摘要',
      dataIndex: 'output_digest',
      key: 'output_digest',
      width: 200,
      ellipsis: true,
      render: (v: string) => (v ? v.slice(0, 16) + '...' : '-'),
    },
    {
      title: '输出数',
      key: 'output_count',
      width: 80,
      align: 'center' as const,
      render: (_: unknown, record: DerivationRun) =>
        Array.isArray(record.outputs) ? record.outputs.length : 0,
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: unknown, record: DerivationRun) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => {
              setSelectedRunId(record.id);
              setActiveTab('graph');
            }}
          >
            查看图谱
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => replayRunMutation.mutate(record.id)}
          >
            重放
          </Button>
        </Space>
      ),
    },
  ];

  const nodeColumns: ColumnsType<ProvenanceNode> = [
    {
      title: '标签',
      dataIndex: 'label',
      key: 'label',
      render: (label: string, record: ProvenanceNode) => {
        if (record.accessible === false) {
          return (
            <Text type="secondary" style={{ color: '#999' }}>
              🔒 无权限节点
              {record.department_name ? `（${record.department_name}）` : ''}
            </Text>
          );
        }
        return label;
      },
    },
    {
      title: '类型',
      dataIndex: 'node_type',
      key: 'node_type',
      width: 120,
      render: (t: string, record: ProvenanceNode) => {
        if (record.accessible === false) {
          return <Text type="secondary">—</Text>;
        }
        return NODE_TYPE_LABEL[t] ?? t;
      },
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 100,
      render: (v: string, record: ProvenanceNode) => {
        if (record.accessible === false) return <Text type="secondary">—</Text>;
        return v;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string, record: ProvenanceNode) => {
        if (record.accessible === false) {
          return <Tag color="default">无权限</Tag>;
        }
        return (
          <Tag color={STATUS_COLOR[s] ?? 'default'}>{STATUS_LABEL[s] ?? s}</Tag>
        );
      },
    },
  ];

  const edgeColumns: ColumnsType<ProvenanceEdge> = [
    { title: '源节点', dataIndex: 'source_id', key: 'source_id' },
    { title: '目标节点', dataIndex: 'target_id', key: 'target_id' },
    { title: '关系类型', dataIndex: 'edge_type', key: 'edge_type' },
  ];

  return (
    <Card>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'evidence-sets',
            label: '证据集',
            children: (
              <div>
                <Space style={{ marginBottom: 16 }}>
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => {
                      evidenceForm.resetFields();
                      setEvidenceModalOpen(true);
                    }}
                  >
                    新建证据集
                  </Button>
                </Space>
                <Table<EvidenceSet>
                  columns={evidenceSetColumns}
                  dataSource={evidenceSets}
                  rowKey="set_id"
                  loading={evidenceLoading}
                  pagination={{ pageSize: 20, showSizeChanger: false }}
                  size="middle"
                />
              </div>
            ),
          },
          {
            key: 'recipes',
            label: '配方',
            children: (
              <div>
                <Space style={{ marginBottom: 16 }}>
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => {
                      recipeForm.resetFields();
                      setRecipeModalOpen(true);
                    }}
                  >
                    新建配方
                  </Button>
                </Space>
                <Table<Recipe>
                  columns={recipeColumns}
                  dataSource={recipes}
                  rowKey="recipe_id"
                  loading={recipesLoading}
                  pagination={{ pageSize: 20, showSizeChanger: false }}
                  size="middle"
                />
              </div>
            ),
          },
          {
            key: 'derivation-runs',
            label: '推导运行',
            children: (
              <div>
                <Space style={{ marginBottom: 16 }}>
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => {
                      runForm.resetFields();
                      setRunModalOpen(true);
                    }}
                  >
                    新建推导运行
                  </Button>
                </Space>
                <Table<DerivationRun>
                  columns={runColumns}
                  dataSource={runs}
                  rowKey="id"
                  loading={runsLoading}
                  pagination={{ pageSize: 20, showSizeChanger: false }}
                  size="middle"
                />
              </div>
            ),
          },
          {
            key: 'graph',
            label: '溯源图谱',
            children: (
              <div>
                <Select
                  placeholder="选择推导运行"
                  style={{ width: 300, marginBottom: 16 }}
                  value={selectedRunId}
                  onChange={(val: string | undefined) => setSelectedRunId(val)}
                  options={runs.map((r) => ({
                    value: r.id,
                    label: `${r.id.slice(0, 8)}... (${STATUS_LABEL[r.status] ?? r.status})`,
                  }))}
                />
                {graph && (
                  <>
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 12 }}
                      message="谱系图展示完整拓扑结构，无权限节点以 🔒 标记显示，不泄露业务内容"
                    />
                    <Title level={5}>节点</Title>
                    <Table<ProvenanceNode>
                      columns={nodeColumns}
                      dataSource={Array.isArray(graph.nodes)
                        ? graph.nodes.map((n) => ({ ...n, key: n.id }))
                        : []}
                      pagination={false}
                      size="small"
                    />
                    <Title level={5} style={{ marginTop: 16 }}>
                      边
                    </Title>
                    <Table<ProvenanceEdge>
                      columns={edgeColumns}
                      dataSource={Array.isArray(graph.edges)
                        ? graph.edges.map((e, idx) => ({
                            ...e,
                            key: `${e.source_id}-${e.target_id}-${idx}`,
                          }))
                        : []}
                      pagination={false}
                      size="small"
                    />
                  </>
                )}
                {!graph && selectedRunId && (
                  <Text type="secondary">加载中...</Text>
                )}
                {!selectedRunId && (
                  <Text type="secondary">请选择一个推导运行</Text>
                )}
              </div>
            ),
          },
        ]}
      />

      {/* 证据集创建 Modal */}
      <Modal
        title="新建证据集"
        open={evidenceModalOpen}
        onOk={handleEvidenceSubmit}
        onCancel={() => {
          setEvidenceModalOpen(false);
          evidenceForm.resetFields();
        }}
        confirmLoading={createEvidenceSetMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Form form={evidenceForm} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="如：材料性能证据集" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 配方创建 Modal */}
      <Modal
        title="新建配方"
        open={recipeModalOpen}
        onOk={handleRecipeSubmit}
        onCancel={() => {
          setRecipeModalOpen(false);
          recipeForm.resetFields();
        }}
        confirmLoading={createRecipeMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Form form={recipeForm} layout="vertical">
          <Form.Item
            name="code"
            label="编码"
            rules={[
              { required: true, message: '请输入编码' },
              {
                pattern: /^[a-z][a-z0-9_]*$/,
                message: '仅小写字母/数字/下划线，首字符必须为字母',
              },
            ]}
          >
            <Input placeholder="如：yield_strength_recipe" maxLength={128} />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="如：屈服强度推导配方" maxLength={256} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 推导运行创建 Modal */}
      <Modal
        title="新建推导运行"
        open={runModalOpen}
        onOk={handleRunSubmit}
        onCancel={() => {
          setRunModalOpen(false);
          runForm.resetFields();
        }}
        confirmLoading={createRunMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Form form={runForm} layout="vertical">
          <Form.Item
            name="evidence_set_version_id"
            label="证据集版本 ID"
            rules={[{ required: true, message: '请输入证据集版本 ID' }]}
          >
            <Input placeholder="如：550e8400-e29b-41d4-a716-446655440000" />
          </Form.Item>
          <Form.Item
            name="recipe_version_id"
            label="配方版本 ID"
            rules={[{ required: true, message: '请输入配方版本 ID' }]}
          >
            <Input placeholder="如：550e8400-e29b-41d4-a716-446655440000" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
