import { useState } from 'react';
import {
  Button,
  Form,
  Input,
  Segmented,
  Select,
  Space,
  Table,
  Tabs,
  Typography,
  message,
} from 'antd';
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
  extractApiError,
  type DerivationRun,
  type EvidenceSet,
  type ProvenanceEdge,
  type ProvenanceNode,
  type Recipe,
} from '@/api/client';
import { DetailSection, DataTableShell, FocusModal, StatusMark } from '@/components/ui';
import type { StatusTone } from '@/theme/tokens';

const { Text } = Typography;

/** 状态 → StatusTone */
const STATUS_TONE: Record<string, StatusTone> = {
  draft: 'info',
  in_review: 'warning',
  published: 'success',
  frozen: 'info',
  active: 'success',
  succeeded: 'success',
  failed: 'danger',
  running: 'info',
  deprecated: 'neutral',
  pending: 'neutral',
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

/** 溯源视图模式 */
type ProvenanceViewMode = 'graph' | 'table';

/**
 * 溯源链路页面
 *
 * 功能：
 * - Tabs: 证据集 / 配方 / 推导运行 / 溯源图谱
 * - 证据集：列表 + 创建 + 冻结
 * - 配方：列表 + 创建 + 发布
 * - 推导运行：列表 + 创建 + 重放 + 查看图谱
 * - 溯源图谱：选择运行后展示节点和边的表格，支持关系图/数据表视图切换
 *
 * Data Ocean Phase 4：用 DetailSection + Segmented 视图切换包裹，
 * 溯源图谱默认提供「关系图 / 数据表」切换，reduced-motion 或图谱初始化失败时默认 table。
 * 保留所有 query / mutation / form 行为不变。
 */
export function ProvenancePage(): JSX.Element {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState('evidence-sets');

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
  const [selectedRunId, setSelectedRunId] = useState<string | undefined>(undefined);
  const [viewMode, setViewMode] = useState<ProvenanceViewMode>('table');

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

  // ---- 检测 reduced-motion，默认 table 视图 ----
  const reducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

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
      width: 120,
      render: (s: string) => (
        <StatusMark tone={STATUS_TONE[s] ?? 'neutral'} label={STATUS_LABEL[s] ?? s} />
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
      width: 120,
      render: (s: string) => (
        <StatusMark tone={STATUS_TONE[s] ?? 'neutral'} label={STATUS_LABEL[s] ?? s} />
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
      width: 120,
      render: (s: string) => (
        <StatusMark tone={STATUS_TONE[s] ?? 'neutral'} label={STATUS_LABEL[s] ?? s} />
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
    { title: '标签', dataIndex: 'label', key: 'label' },
    {
      title: '类型',
      dataIndex: 'node_type',
      key: 'node_type',
      width: 120,
      render: (t: string) => NODE_TYPE_LABEL[t] ?? t,
    },
    { title: '版本', dataIndex: 'version', key: 'version', width: 100 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (s: string) => (
        <StatusMark tone={STATUS_TONE[s] ?? 'neutral'} label={STATUS_LABEL[s] ?? s} />
      ),
    },
  ];

  const edgeColumns: ColumnsType<ProvenanceEdge> = [
    { title: '源节点', dataIndex: 'source_id', key: 'source_id' },
    { title: '目标节点', dataIndex: 'target_id', key: 'target_id' },
    { title: '关系类型', dataIndex: 'edge_type', key: 'edge_type' },
  ];

  // 图谱数据存在时才有 graph 视图
  const hasGraphData = graph && Array.isArray(graph.nodes) && graph.nodes.length > 0;
  const effectiveViewMode: ProvenanceViewMode = (reducedMotion || !hasGraphData) ? 'table' : viewMode;

  return (
    <div>
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
                    onClick={() => {
                      evidenceForm.resetFields();
                      setEvidenceModalOpen(true);
                    }}
                  >
                    新建证据集
                  </Button>
                </Space>
                <DataTableShell>
                  <Table<EvidenceSet>
                    columns={evidenceSetColumns}
                    dataSource={evidenceSets}
                    rowKey="set_id"
                    loading={evidenceLoading}
                    pagination={{ pageSize: 20, showSizeChanger: false }}
                    size="middle"
                    locale={{ emptyText: '暂无证据集' }}
                  />
                </DataTableShell>
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
                    onClick={() => {
                      recipeForm.resetFields();
                      setRecipeModalOpen(true);
                    }}
                  >
                    新建配方
                  </Button>
                </Space>
                <DataTableShell>
                  <Table<Recipe>
                    columns={recipeColumns}
                    dataSource={recipes}
                    rowKey="recipe_id"
                    loading={recipesLoading}
                    pagination={{ pageSize: 20, showSizeChanger: false }}
                    size="middle"
                  />
                </DataTableShell>
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
                    onClick={() => {
                      runForm.resetFields();
                      setRunModalOpen(true);
                    }}
                  >
                    新建推导运行
                  </Button>
                </Space>
                <DataTableShell>
                  <Table<DerivationRun>
                    columns={runColumns}
                    dataSource={runs}
                    rowKey="id"
                    loading={runsLoading}
                    pagination={{ pageSize: 20, showSizeChanger: false }}
                    size="middle"
                  />
                </DataTableShell>
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
                    <div style={{ marginBottom: 16 }}>
                      <Segmented
                        aria-label="溯源视图"
                        options={[{ label: '关系图', value: 'graph' }, { label: '数据表', value: 'table' }]}
                        value={effectiveViewMode}
                        onChange={(value) => setViewMode(value as 'graph' | 'table')}
                      />
                    </div>

                    {effectiveViewMode === 'table' && (
                      <>
                        <DetailSection title="节点">
                          <Table<ProvenanceNode>
                            columns={nodeColumns}
                            dataSource={Array.isArray(graph.nodes)
                              ? graph.nodes.map((n) => ({ ...n, key: n.id }))
                              : []}
                            pagination={false}
                            size="small"
                          />
                        </DetailSection>

                        <DetailSection title="边">
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
                        </DetailSection>
                      </>
                    )}

                    {effectiveViewMode === 'graph' && (
                      <DetailSection title="关系图" technical>
                        <Text type="secondary">
                          关系图视图暂以数据表形式展示。节点和边 ID/类型可复制。
                        </Text>
                        <div style={{ marginTop: 12 }}>
                          <Table<ProvenanceNode>
                            columns={nodeColumns}
                            dataSource={Array.isArray(graph.nodes)
                              ? graph.nodes.map((n) => ({ ...n, key: n.id }))
                              : []}
                            pagination={false}
                            size="small"
                          />
                        </div>
                      </DetailSection>
                    )}
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
      <FocusModal
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
      </FocusModal>

      {/* 配方创建 Modal */}
      <FocusModal
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
      </FocusModal>

      {/* 推导运行创建 Modal */}
      <FocusModal
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
      </FocusModal>
    </div>
  );
}
