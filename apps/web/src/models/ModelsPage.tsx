import { useState } from 'react';
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateModel,
  apiDeprecateModel,
  apiGetModelVersions,
  apiListModels,
  apiPublishModelVersion,
  apiRollbackModel,
  extractApiError,
  type ModelSummary,
  type ModelVersionSummary,
} from '@/api/client';

const { Title, Text } = Typography;

/** 模型状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  draft: 'blue',
  pending_validation: 'orange',
  validated: 'cyan',
  published: 'green',
  deprecated: 'default',
};

/** 模型状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  pending_validation: '待验证',
  validated: '已验证',
  published: '已发布',
  deprecated: '已废弃',
};

/** 版本状态 → 中文标签 */
const VERSION_STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  pending_validation: '待验证',
  validated: '已验证',
  published: '已发布',
  deprecated: '已废弃',
};

/**
 * 模型管理页面（IRIP V2-T05）
 *
 * 功能：
 * - Ant Design Table 列表（编码 / 名称 / 状态 / 当前版本）
 * - 顶部「新建模型」按钮 → Modal
 * - 操作列：查看详情 / 发布 / 回滚 / 废弃
 * - 状态颜色映射：draft=blue, pending_validation=orange, validated=cyan,
 *   published=green, deprecated=default
 */
export function ModelsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [publishModel, setPublishModel] = useState<ModelSummary | null>(null);
  const [rollbackModel, setRollbackModel] = useState<ModelSummary | null>(null);
  const [publishVersionId, setPublishVersionId] = useState<string | undefined>(undefined);
  const [rollbackVersionId, setRollbackVersionId] = useState<string | undefined>(undefined);
  const [createForm] = Form.useForm();

  // ---- 模型列表查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['models'],
    queryFn: () => apiListModels(),
  });

  const items: ModelSummary[] = data?.items ?? [];

  // ---- 发布 Modal：获取版本列表 ----
  const { data: publishVersions } = useQuery({
    queryKey: ['model-versions', publishModel?.id],
    queryFn: () => apiGetModelVersions(publishModel!.id),
    enabled: !!publishModel,
  });

  // ---- 回滚 Modal：获取版本列表 ----
  const { data: rollbackVersions } = useQuery({
    queryKey: ['model-versions', rollbackModel?.id],
    queryFn: () => apiGetModelVersions(rollbackModel!.id),
    enabled: !!rollbackModel,
  });

  // ---- 创建模型 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateModel,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['models'] });
      setCreateModalOpen(false);
      createForm.resetFields();
      message.success('模型创建成功');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 发布版本 Mutation ----
  const publishVersionMutation = useMutation({
    mutationFn: (vars: { modelId: string; versionId: string }) =>
      apiPublishModelVersion(vars.modelId, vars.versionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['models'] });
      setPublishModel(null);
      setPublishVersionId(undefined);
      message.success('模型版本已发布');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 回滚 Mutation ----
  const rollbackMutation = useMutation({
    mutationFn: (vars: { modelId: string; versionId: string }) =>
      apiRollbackModel(vars.modelId, vars.versionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['models'] });
      setRollbackModel(null);
      setRollbackVersionId(undefined);
      message.success('模型已回滚');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 废弃 Mutation ----
  const deprecateMutation = useMutation({
    mutationFn: apiDeprecateModel,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['models'] });
      message.success('模型已废弃');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 事件处理 ----
  const handleCreate = async (): Promise<void> => {
    try {
      const values = await createForm.validateFields();
      createMutation.mutate({ code: values.code, display_name: values.display_name });
    } catch {
      // 校验失败
    }
  };

  const handlePublish = (): void => {
    if (publishModel && publishVersionId) {
      publishVersionMutation.mutate({
        modelId: publishModel.id,
        versionId: publishVersionId,
      });
    }
  };

  const handleRollback = (): void => {
    if (rollbackModel && rollbackVersionId) {
      rollbackMutation.mutate({
        modelId: rollbackModel.id,
        versionId: rollbackVersionId,
      });
    }
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<ModelSummary> = [
    {
      title: '编码',
      dataIndex: 'code',
      key: 'code',
      width: 180,
      render: (v: string) => <Text code>{v}</Text>,
    },
    { title: '名称', dataIndex: 'display_name', key: 'display_name' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] ?? 'default'}>
          {STATUS_LABEL[v] ?? v}
        </Tag>
      ),
    },
    {
      title: '当前版本',
      key: 'current_version',
      width: 140,
      render: (_: unknown, record: ModelSummary) =>
        record.current_version_id ? (
          <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>
            {record.current_version_id.slice(0, 12)}…
          </Text>
        ) : (
          '-'
        ),
    },
    {
      title: '操作',
      key: 'action',
      width: 280,
      render: (_: unknown, record: ModelSummary) => (
        <Space size="small" wrap>
          <Button
            type="link"
            size="small"
            onClick={() => void navigate({ to: '/models/$modelId', params: { modelId: record.id } })}
          >
            查看详情
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => {
              setPublishModel(record);
              setPublishVersionId(undefined);
            }}
          >
            发布
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => {
              setRollbackModel(record);
              setRollbackVersionId(undefined);
            }}
          >
            回滚
          </Button>
          {record.status !== 'deprecated' && (
            <Popconfirm
              title="确认废弃该模型？"
              onConfirm={() => deprecateMutation.mutate(record.id)}
              okText="确定"
              cancelText="取消"
            >
              <Button type="link" size="small" danger>
                废弃
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  // ---- 版本 Select 选项 ----
  const buildVersionOptions = (versions: ModelVersionSummary[] | undefined) =>
    (versions ?? []).map((v) => ({
      value: v.id,
      label: `v${v.version} — ${VERSION_STATUS_LABEL[v.status] ?? v.status}`,
    }));

  return (
    <div>
      <Title level={2}>模型管理</Title>

      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={() => setCreateModalOpen(true)}>
          新建模型
        </Button>
        <Button onClick={() => void navigate({ to: '/models/predict' })}>
          预测工作台
        </Button>
      </Space>

      <Card>
        <Table<ModelSummary>
          columns={columns}
          dataSource={items}
          rowKey="id"
          loading={isLoading}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="middle"
        />
      </Card>

      {/* 新建模型 Modal */}
      <Modal
        title="新建模型"
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
            label="模型编码"
            rules={[
              { required: true, message: '请输入模型编码' },
              {
                pattern: /^[a-z][a-z0-9_]*$/,
                message: '仅小写字母/数字/下划线，首字符必须为字母',
              },
            ]}
          >
            <Input placeholder="如：grate_cooler_rom" maxLength={128} />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="模型名称"
            rules={[{ required: true, message: '请输入模型名称' }]}
          >
            <Input placeholder="如：篦冷机降阶模型" maxLength={256} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 发布版本 Modal */}
      <Modal
        title={`发布模型版本 — ${publishModel?.display_name ?? ''}`}
        open={!!publishModel}
        onOk={handlePublish}
        onCancel={() => {
          setPublishModel(null);
          setPublishVersionId(undefined);
        }}
        confirmLoading={publishVersionMutation.isPending}
        okText="发布"
        cancelText="取消"
        okButtonProps={{ disabled: !publishVersionId }}
      >
        <Form layout="vertical">
          <Form.Item label="选择版本（已验证版本可发布）">
            <Select
              placeholder="选择要发布的版本"
              style={{ width: '100%' }}
              value={publishVersionId}
              onChange={(val: string) => setPublishVersionId(val)}
              options={buildVersionOptions(publishVersions)}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 回滚 Modal */}
      <Modal
        title={`回滚模型 — ${rollbackModel?.display_name ?? ''}`}
        open={!!rollbackModel}
        onOk={handleRollback}
        onCancel={() => {
          setRollbackModel(null);
          setRollbackVersionId(undefined);
        }}
        confirmLoading={rollbackMutation.isPending}
        okText="回滚"
        cancelText="取消"
        okButtonProps={{ disabled: !rollbackVersionId }}
      >
        <Form layout="vertical">
          <Form.Item label="选择目标版本">
            <Select
              placeholder="选择回滚目标版本"
              style={{ width: '100%' }}
              value={rollbackVersionId}
              onChange={(val: string) => setRollbackVersionId(val)}
              options={buildVersionOptions(rollbackVersions)}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

export default ModelsPage;
