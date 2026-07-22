import { useState } from 'react';
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiGetComponent,
  apiListComponents,
  apiPublishComponent,
  extractApiError,
  type ComponentDetail,
  type ComponentSummary,
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

/**
 * 组件管理页面（IRIP V2-T05）
 *
 * 功能：
 * - Ant Design Table 列表展示已注册组件（名称 / 版本 / 类别 / 运行时 / 状态）
 * - 顶部「注册组件」按钮 → Modal（粘贴 YAML manifest）
 * - 类别筛选 Select
 * - 点击行 → Drawer 展示组件 manifest 详情
 * - 使用 TanStack Query 管理数据
 */
export function ComponentsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [kindFilter, setKindFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [detailId, setDetailId] = useState<string | null>(null);

  // ---- 列表查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['components', kindFilter],
    queryFn: () => apiListComponents({ kind: kindFilter }),
  });

  const items: ComponentSummary[] = data?.items ?? [];

  // ---- 详情查询 ----
  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['component', detailId],
    queryFn: () => apiGetComponent(detailId!),
    enabled: !!detailId,
  });

  // ---- 发布组件 Mutation ----
  const publishMutation = useMutation({
    mutationFn: apiPublishComponent,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      setModalOpen(false);
      form.resetFields();
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
        <Tag color="blue">{KIND_LABEL[v] ?? v}</Tag>
      ),
    },
    {
      title: '运行时',
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
      width: 100,
      render: (_: unknown, record: ComponentSummary) => (
        <Button
          type="link"
          size="small"
          onClick={() => setDetailId(record.id)}
        >
          查看详情
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
        <Table<ComponentSummary>
          columns={columns}
          dataSource={items}
          rowKey="id"
          loading={isLoading}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="middle"
          onRow={(record) => ({
            onClick: () => setDetailId(record.id),
            style: { cursor: 'pointer' },
          })}
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
      >
        {detail && <ComponentDetailPanel detail={detail} />}
      </Drawer>
    </div>
  );
}

/** 组件详情面板 */
function ComponentDetailPanel({ detail }: { detail: ComponentDetail }): JSX.Element {
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
        <Descriptions.Item label="运行时">
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
          maxHeight: 480,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {detail.manifest_yaml}
      </pre>
    </div>
  );
}
