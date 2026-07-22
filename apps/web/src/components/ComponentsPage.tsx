import { useState } from 'react';
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiGetComponent,
  apiListComponentVersions,
  apiListComponents,
  apiPublishComponent,
  extractApiError,
  type ComponentDetail,
  type ComponentSummary,
  type ComponentVersionItem,
} from '@/api/client';

const { Title, Text } = Typography;

/** 组件类别 → 中文标签 */
const KIND_LABEL: Record<string, string> = {
  ingestion: '数据接入',
  transform: '数据转换',
  quality: '质量校验',
  statistics: '统计分析',
  output: '结果输出',
  model: '模型推理',
};

/** 组件状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  draft: 'blue',
  published: 'green',
  deprecated: 'default',
};

/** 组件状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  deprecated: '已弃用',
};

/** LLM 驱动的组件名称集合 */
const LLM_COMPONENTS = new Set(['llm_extractor']);

/**
 * 组件管理页面
 *
 * 分两栏展示：
 * - 摩登：基于 LLM 的组件（如 llm_extractor）
 * - 古法：基于代码的经典组件（csv_reader 等）
 */
export function ComponentsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<'modern' | 'classic'>('modern');
  const [kindFilter, setKindFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [detailId, setDetailId] = useState<string | null>(null);

  // ---- 列表查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['components', kindFilter],
    queryFn: () => apiListComponents({ kind: kindFilter }),
  });

  const allItems: ComponentSummary[] = data?.items ?? [];

  // 按摩登/古法分组
  const modernItems = allItems.filter((i) => LLM_COMPONENTS.has(i.name));
  const classicItems = allItems.filter((i) => !LLM_COMPONENTS.has(i.name));
  const currentItems = activeTab === 'modern' ? modernItems : classicItems;

  // ---- 详情查询 ----
  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['component', detailId],
    queryFn: () => apiGetComponent(detailId!),
    enabled: !!detailId,
  });

  // ---- 发布组件 Mutation（注册 + 编辑共用）----
  const publishMutation = useMutation({
    mutationFn: apiPublishComponent,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      setModalOpen(false);
      setEditModalOpen(false);
      form.resetFields();
      editForm.resetFields();
      message.success('组件发布成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 事件处理 ----
  const handleOpenModal = (): void => {
    form.resetFields();
    setModalOpen(true);
  };

  const handlePublish = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      publishMutation.mutate({ manifest_yaml: values.manifest_yaml });
    } catch {
      // 表单校验失败
    }
  };

  const handleOpenEdit = (): void => {
    if (!detail) return;
    // 自动递增版本号（如 1.0.0 → 1.0.1）
    let yaml = detail.manifest_yaml;
    const versionMatch = yaml.match(/^version:\s*["']?(\d+)\.(\d+)\.(\d+)["']?/m);
    if (versionMatch) {
      const newVersion = `${versionMatch[1]}.${versionMatch[2]}.${Number(versionMatch[3]) + 1}`;
      yaml = yaml.replace(/^version:\s*["']?\d+\.\d+\.\d+["']?/m, `version: "${newVersion}"`);
    }
    editForm.setFieldsValue({ manifest_yaml: yaml });
    setEditModalOpen(true);
  };

  const handleEditPublish = async (): Promise<void> => {
    try {
      const values = await editForm.validateFields();
      publishMutation.mutate({ manifest_yaml: values.manifest_yaml });
    } catch {
      // 表单校验失败
    }
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<ComponentSummary> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (v: string) => <Text strong>{v}</Text>,
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 100,
    },
    {
      title: '类别',
      dataIndex: 'kind',
      key: 'kind',
      width: 120,
      render: (v: string) => (
        <Tag color={activeTab === 'modern' ? 'purple' : 'blue'}>
          {KIND_LABEL[v] ?? v}
        </Tag>
      ),
    },
    {
      title: '执行引擎',
      dataIndex: 'runtime',
      key: 'runtime',
      width: 120,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] ?? 'default'}>
          {STATUS_LABEL[v] ?? v}
        </Tag>
      ),
    },
    {
      title: 'SHA-256',
      dataIndex: 'manifest_sha256',
      key: 'manifest_sha256',
      width: 140,
      ellipsis: true,
      render: (v: string) => (
        <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>
          {v ? `${v.slice(0, 12)}…` : '-'}
        </Text>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: unknown, record: ComponentSummary) => (
        <Button
          type="link"
          size="small"
          onClick={() => setDetailId(record.id)}
        >
          详情
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Title level={2}>组件管理</Title>

      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={handleOpenModal}>
          注册组件
        </Button>
        <Select
          placeholder="类别筛选"
          allowClear
          style={{ width: 160 }}
          value={kindFilter}
          onChange={(val: string | undefined) => setKindFilter(val)}
          options={[
            { value: 'ingestion', label: '数据接入' },
            { value: 'transform', label: '数据转换' },
            { value: 'quality', label: '质量校验' },
            { value: 'statistics', label: '统计分析' },
            { value: 'output', label: '结果输出' },
            { value: 'model', label: '模型推理' },
          ]}
        />
      </Space>

      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key as 'modern' | 'classic')}
          items={[
            {
              key: 'modern',
              label: (
                <span>
                  <Tag color="purple" style={{ marginRight: 4 }}>AI</Tag>
                  摩登
                  <Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>
                    ({modernItems.length})
                  </Text>
                </span>
              ),
              children: (
                <Table<ComponentSummary>
                  columns={columns}
                  dataSource={currentItems}
                  rowKey="id"
                  loading={isLoading}
                  pagination={{ pageSize: 20, showSizeChanger: false }}
                  size="middle"
                  onRow={(record) => ({
                    onClick: () => setDetailId(record.id),
                    style: { cursor: 'pointer' },
                  })}
                />
              ),
            },
            {
              key: 'classic',
              label: (
                <span>
                  <Tag color="blue" style={{ marginRight: 4 }}>Code</Tag>
                  古法
                  <Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>
                    ({classicItems.length})
                  </Text>
                </span>
              ),
              children: (
                <Table<ComponentSummary>
                  columns={columns}
                  dataSource={currentItems}
                  rowKey="id"
                  loading={isLoading}
                  pagination={{ pageSize: 20, showSizeChanger: false }}
                  size="middle"
                  onRow={(record) => ({
                    onClick: () => setDetailId(record.id),
                    style: { cursor: 'pointer' },
                  })}
                />
              ),
            },
          ]}
        />
      </Card>

      {/* 注册组件 Modal */}
      <Modal
        title="注册组件"
        open={modalOpen}
        onOk={handlePublish}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        confirmLoading={publishMutation.isPending}
        okText="发布"
        cancelText="取消"
        width={680}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="manifest_yaml"
            label="组件清单 (YAML)"
            rules={[
              { required: true, message: '请粘贴组件清单 YAML' },
              { min: 10, message: '清单内容过短' },
            ]}
          >
            <Input.TextArea
              placeholder={`name: my_component\nversion: "1.0.0"\nkind: transform\nruntime: python\n...`}
              rows={16}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 组件详情 Drawer */}
      <Drawer
        title="组件详情"
        open={!!detailId}
        onClose={() => setDetailId(null)}
        width={640}
        loading={detailLoading}
        extra={
          detail && (
            <Button type="primary" size="small" onClick={handleOpenEdit}>
              编辑
            </Button>
          )
        }
      >
        {detail && <ComponentDetailPanel detail={detail} detailId={detailId!} />}
      </Drawer>

      {/* 编辑组件 Modal */}
      <Modal
        title="编辑组件"
        open={editModalOpen}
        onOk={handleEditPublish}
        onCancel={() => {
          setEditModalOpen(false);
          editForm.resetFields();
        }}
        confirmLoading={publishMutation.isPending}
        okText="发布新版本"
        cancelText="取消"
        width={680}
      >
        <Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
          修改 YAML 后点击发布，将创建新版本。版本号已自动递增，如需修改请手动调整。
        </Text>
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="manifest_yaml"
            label="组件清单 (YAML)"
            rules={[
              { required: true, message: '请输入组件清单 YAML' },
              { min: 10, message: '清单内容过短' },
            ]}
          >
            <Input.TextArea
              rows={20}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

/** 组件详情面板 */
function ComponentDetailPanel({
  detail,
  detailId,
}: {
  detail: ComponentDetail;
  detailId: string;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [rollbackVersion, setRollbackVersion] = useState<string | null>(null);

  // ---- 版本历史查询 ----
  const { data: versions, isLoading: versionsLoading } = useQuery({
    queryKey: ['component-versions', detailId],
    queryFn: () => apiListComponentVersions(detailId),
  });

  // ---- 回滚 Mutation（用旧版本 manifest 重新发布）----
  const rollbackMutation = useMutation({
    mutationFn: async (versionId: string) => {
      // 获取旧版本详情（拿 manifest_yaml）
      const oldDetail = await apiGetComponent(versionId);
      const oldManifest = oldDetail.manifest_yaml;
      // 自动递增版本号
      let yaml = oldManifest;
      const versionMatch = yaml.match(/^version:\s*["']?(\d+)\.(\d+)\.(\d+)["']?/m);
      if (versionMatch) {
        const newVersion = `${versionMatch[1]}.${versionMatch[2]}.${Number(versionMatch[3]) + 1}`;
        yaml = yaml.replace(/^version:\s*["']?\d+\.\d+\.\d+["']?/m, `version: "${newVersion}"`);
      }
      return apiPublishComponent({ manifest_yaml: yaml });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      void queryClient.invalidateQueries({ queryKey: ['component-versions', detailId] });
      setRollbackVersion(null);
      message.success('已回滚并发布新版本');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  return (
    <div>
      <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="名称">
          <Text strong>{detail.name}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="版本">{detail.version}</Descriptions.Item>
        <Descriptions.Item label="类别">
          <Tag color="blue">{KIND_LABEL[detail.kind] ?? detail.kind}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="执行引擎">
          <Text code>{detail.runtime}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={STATUS_COLOR[detail.status] ?? 'default'}>
            {STATUS_LABEL[detail.status] ?? detail.status}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="SHA-256">
          <Text
            copyable
            style={{ fontFamily: 'monospace', fontSize: 12 }}
          >
            {detail.manifest_sha256}
          </Text>
        </Descriptions.Item>
        <Descriptions.Item label="发布时间">
          {detail.published_at ?? '-'}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {detail.created_at}
        </Descriptions.Item>
      </Descriptions>

      <Title level={5} style={{ marginTop: 24 }}>
        Manifest (YAML)
      </Title>
      <pre
        style={{
          background: '#f5f5f5',
          padding: 16,
          borderRadius: 6,
          fontSize: 13,
          fontFamily: 'monospace',
          overflow: 'auto',
          maxHeight: 320,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {detail.manifest_yaml}
      </pre>

      {/* 版本历史 */}
      <Title level={5} style={{ marginTop: 24 }}>
        版本历史
      </Title>
      {versionsLoading ? (
        <div style={{ textAlign: 'center', padding: 16 }}>
          <Spin size="small" />
        </div>
      ) : versions && versions.length > 0 ? (
        <div style={{ maxHeight: 300, overflow: 'auto' }}>
          {versions.map((v: ComponentVersionItem) => {
            const isCurrent = v.id === detailId;
            return (
              <div
                key={v.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '8px 12px',
                  borderBottom: '1px solid #f0f0f0',
                  background: isCurrent ? '#f6ffed' : 'transparent',
                }}
              >
                <Space size={8}>
                  <Tag color={isCurrent ? 'green' : 'default'}>
                    v{v.version}
                  </Tag>
                  {isCurrent && (
                    <Text type="success" style={{ fontSize: 11 }}>
                      当前
                    </Text>
                  )}
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {v.created_at.slice(0, 19)}
                  </Text>
                </Space>
                {!isCurrent && (
                  <Popconfirm
                    title={`回滚到 v${v.version}？`}
                    description="将用该版本的 manifest 发布一个新版本号"
                    onConfirm={() => setRollbackVersion(v.id)}
                    okText="回滚"
                    cancelText="取消"
                  >
                    <Button
                      type="link"
                      size="small"
                      loading={rollbackVersion === v.id && rollbackMutation.isPending}
                    >
                      回滚
                    </Button>
                  </Popconfirm>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }}>
          暂无其他版本
        </Text>
      )}
    </div>
  );
}
