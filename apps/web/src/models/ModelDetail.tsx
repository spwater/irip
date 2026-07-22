import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import type { ColumnsType } from 'antd/es/table';
import {
  apiGetModel,
  apiGetModelVersions,
  apiPublishModelVersion,
  apiRollbackModel,
  apiValidateModelVersion,
  extractApiError,
  type ModelVersionSummary,
} from '@/api/client';

const { Text, Paragraph } = Typography;

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

/** 适用域条目（从 applicability_domain 字段解析） */
type ApplicabilityEntry = {
  field: string;
  min: number | null;
  max: number | null;
  unit: string | null;
};

/**
 * 模型详情页面（IRIP V2-T05）
 *
 * 功能：
 * - 基本信息 + 状态标签
 * - 版本历史 Table（版本号 / 状态 / 指标 / 发布时间）
 * - 适用域范围展示
 * - 操作按钮：提交验证 / 发布 / 回滚
 */
export function ModelDetail(): JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const params = useParams({ strict: false });
  const modelId = String((params as Record<string, unknown>).modelId ?? '');

  const [validateModalOpen, setValidateModalOpen] = useState(false);
  const [publishModalOpen, setPublishModalOpen] = useState(false);
  const [rollbackModalOpen, setRollbackModalOpen] = useState(false);
  const [selectedVersionId, setSelectedVersionId] = useState<string | undefined>(undefined);
  const [rollbackVersionId, setRollbackVersionId] = useState<string | undefined>(undefined);
  const [validateForm] = Form.useForm();

  // ---- 模型详情查询 ----
  const { data: model, isLoading: modelLoading } = useQuery({
    queryKey: ['model', modelId],
    queryFn: () => apiGetModel(modelId),
    enabled: !!modelId,
  });

  // ---- 版本列表查询 ----
  const { data: versions, isLoading: versionsLoading } = useQuery({
    queryKey: ['model-versions', modelId],
    queryFn: () => apiGetModelVersions(modelId),
    enabled: !!modelId,
  });

  const versionList: ModelVersionSummary[] = versions ?? [];

  // 当前版本（用于展示适用域）
  const currentVersion: ModelVersionSummary | undefined = model?.current_version_id
    ? versionList.find((v) => v.id === model.current_version_id)
    : undefined;

  // ---- 提交验证 Mutation ----
  const validateMutation = useMutation({
    mutationFn: (vars: {
      modelId: string;
      versionId: string;
      body: Parameters<typeof apiValidateModelVersion>[2];
    }) => apiValidateModelVersion(vars.modelId, vars.versionId, vars.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['model-versions', modelId] });
      void queryClient.invalidateQueries({ queryKey: ['model', modelId] });
      setValidateModalOpen(false);
      setSelectedVersionId(undefined);
      validateForm.resetFields();
      message.success('版本验证已提交');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 发布版本 Mutation ----
  const publishMutation = useMutation({
    mutationFn: (vars: { modelId: string; versionId: string }) =>
      apiPublishModelVersion(vars.modelId, vars.versionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['model', modelId] });
      void queryClient.invalidateQueries({ queryKey: ['model-versions', modelId] });
      setPublishModalOpen(false);
      setSelectedVersionId(undefined);
      message.success('模型版本已发布');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 回滚 Mutation ----
  const rollbackMutation = useMutation({
    mutationFn: (vars: { modelId: string; versionId: string }) =>
      apiRollbackModel(vars.modelId, vars.versionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['model', modelId] });
      void queryClient.invalidateQueries({ queryKey: ['model-versions', modelId] });
      setRollbackModalOpen(false);
      setRollbackVersionId(undefined);
      message.success('模型已回滚');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 事件处理 ----
  const handleValidate = async (): Promise<void> => {
    if (!selectedVersionId) return;
    try {
      const values = await validateForm.validateFields();
      const metrics = values.metrics_json ? JSON.parse(values.metrics_json as string) : undefined;
      const ad = values.applicability_domain_json
        ? JSON.parse(values.applicability_domain_json as string)
        : undefined;
      validateMutation.mutate({
        modelId,
        versionId: selectedVersionId,
        body: {
          dataset_artifact_id: values.dataset_artifact_id || undefined,
          metrics,
          applicability_domain: ad,
        },
      });
    } catch (err) {
      if (err instanceof Error && err.message.includes('JSON')) {
        message.error(`JSON 解析失败: ${err.message}`);
      }
    }
  };

  const handlePublish = (): void => {
    if (selectedVersionId) {
      publishMutation.mutate({ modelId, versionId: selectedVersionId });
    }
  };

  const handleRollback = (): void => {
    if (rollbackVersionId) {
      rollbackMutation.mutate({ modelId, versionId: rollbackVersionId });
    }
  };

  // ---- 加载与空状态 ----
  if (modelLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!model) {
    return <Empty description="未找到模型" />;
  }

  // ---- 版本历史表格列 ----
  const versionColumns: ColumnsType<ModelVersionSummary> = [
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 80,
      render: (v: number) => <Text strong>v{v}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] ?? 'default'}>{STATUS_LABEL[v] ?? v}</Tag>
      ),
    },
    {
      title: '指标',
      key: 'metrics',
      render: (_: unknown, record: ModelVersionSummary) => {
        const entries = Object.entries(record.metrics);
        if (entries.length === 0) return <Text type="secondary">-</Text>;
        return (
          <Space size="small" wrap>
            {entries.map(([k, val]) => (
              <Tag key={k} color="blue">
                {k}: {String(val)}
              </Tag>
            ))}
          </Space>
        );
      },
    },
    {
      title: '发布时间',
      dataIndex: 'published_at',
      key: 'published_at',
      width: 180,
      render: (v: string | null) => v ?? '-',
    },
  ];

  // ---- 适用域解析 ----
  const applicabilityEntries: ApplicabilityEntry[] = currentVersion
    ? parseApplicabilityDomain(currentVersion.applicability_domain)
    : [];

  // ---- 版本 Select 选项 ----
  const versionOptions = versionList.map((v) => ({
    value: v.id,
    label: `v${v.version} — ${STATUS_LABEL[v.status] ?? v.status}`,
  }));

  return (
    <div>
      <Button
        onClick={() => void navigate({ to: '/models' })}
        style={{ marginBottom: 16 }}
      >
        返回列表
      </Button>

      {/* 基本信息 */}
      <Card title="模型详情" style={{ marginBottom: 16 }}>
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="编码">
            <Text code>{model.code}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="名称">
            <Text strong>{model.display_name}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={STATUS_COLOR[model.status] ?? 'default'}>
              {STATUS_LABEL[model.status] ?? model.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="当前版本">
            {model.current_version_id ? (
              <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>
                {model.current_version_id.slice(0, 12)}…
              </Text>
            ) : (
              '未发布'
            )}
          </Descriptions.Item>
          <Descriptions.Item label="锁版本">{model.lock_version}</Descriptions.Item>
          <Descriptions.Item label="更新时间">{model.updated_at}</Descriptions.Item>
        </Descriptions>

        <Space style={{ marginTop: 16 }} wrap>
          <Button
            onClick={() => {
              setSelectedVersionId(undefined);
              validateForm.resetFields();
              setValidateModalOpen(true);
            }}
          >
            提交验证
          </Button>
          <Button
            type="primary"
            onClick={() => {
              setSelectedVersionId(undefined);
              setPublishModalOpen(true);
            }}
          >
            发布版本
          </Button>
          <Button
            onClick={() => {
              setRollbackVersionId(undefined);
              setRollbackModalOpen(true);
            }}
          >
            回滚
          </Button>
          <Button onClick={() => void navigate({ to: '/models/predict', search: { modelId: model.id } })}>
            前往预测
          </Button>
        </Space>
      </Card>

      {/* 适用域范围 */}
      {currentVersion && (
        <Card title="适用域范围（当前版本）" style={{ marginBottom: 16 }}>
          {applicabilityEntries.length === 0 ? (
            <Text type="secondary">暂无适用域定义</Text>
          ) : (
            <Descriptions bordered column={2} size="small">
              {applicabilityEntries.map((entry) => (
                <Descriptions.Item key={entry.field} label={entry.field}>
                  <Text>
                    {entry.min ?? '−∞'} ~ {entry.max ?? '+∞'}
                  </Text>
                  {entry.unit && (
                    <Text type="secondary"> {entry.unit}</Text>
                  )}
                </Descriptions.Item>
              ))}
            </Descriptions>
          )}
        </Card>
      )}

      {/* 版本历史 */}
      <Card title="版本历史" style={{ marginBottom: 16 }}>
        <Table<ModelVersionSummary>
          columns={versionColumns}
          dataSource={versionList}
          rowKey="id"
          loading={versionsLoading}
          pagination={false}
          size="small"
        />
        {versionList.length === 0 && (
          <Paragraph type="secondary">暂无版本记录</Paragraph>
        )}
      </Card>

      {/* 提交验证 Modal */}
      <Modal
        title="提交版本验证"
        open={validateModalOpen}
        onOk={handleValidate}
        onCancel={() => {
          setValidateModalOpen(false);
          setSelectedVersionId(undefined);
          validateForm.resetFields();
        }}
        confirmLoading={validateMutation.isPending}
        okText="提交"
        cancelText="取消"
        width={640}
        okButtonProps={{ disabled: !selectedVersionId }}
      >
        <Form form={validateForm} layout="vertical">
          <Form.Item label="选择版本">
            <Select
              placeholder="选择要验证的版本"
              style={{ width: '100%' }}
              value={selectedVersionId}
              onChange={(val: string) => setSelectedVersionId(val)}
              options={versionOptions}
            />
          </Form.Item>
          <Form.Item name="dataset_artifact_id" label="验证数据集工件 ID（可选）">
            <Input placeholder="UUID 格式" />
          </Form.Item>
          <Form.Item name="metrics_json" label="验证指标 (JSON，可选)">
            <Input.TextArea
              rows={4}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder={`{\n  "rmse": 0.05,\n  "r2": 0.98\n}`}
            />
          </Form.Item>
          <Form.Item name="applicability_domain_json" label="适用域 (JSON，可选)">
            <Input.TextArea
              rows={4}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder={`{\n  "temperature": {"min": 120, "max": 300, "unit": "C"}\n}`}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 发布版本 Modal */}
      <Modal
        title="发布模型版本"
        open={publishModalOpen}
        onOk={handlePublish}
        onCancel={() => {
          setPublishModalOpen(false);
          setSelectedVersionId(undefined);
        }}
        confirmLoading={publishMutation.isPending}
        okText="发布"
        cancelText="取消"
        okButtonProps={{ disabled: !selectedVersionId }}
      >
        <Form layout="vertical">
          <Form.Item label="选择已验证版本">
            <Select
              placeholder="选择要发布的版本"
              style={{ width: '100%' }}
              value={selectedVersionId}
              onChange={(val: string) => setSelectedVersionId(val)}
              options={versionOptions}
            />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            message="发布后该版本将成为当前发布版本，可用于预测。"
          />
        </Form>
      </Modal>

      {/* 回滚 Modal */}
      <Modal
        title="回滚模型"
        open={rollbackModalOpen}
        onOk={handleRollback}
        onCancel={() => {
          setRollbackModalOpen(false);
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
              options={versionOptions}
            />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            message="回滚将把发布指针移动到所选版本。"
          />
        </Form>
      </Modal>
    </div>
  );
}

/**
 * 解析适用域字典为条目列表。
 *
 * 适用域格式：`{ field_name: { min, max, unit } }`。
 */
function parseApplicabilityDomain(
  domain: Record<string, unknown>,
): ApplicabilityEntry[] {
  const entries: ApplicabilityEntry[] = [];
  for (const [field, spec] of Object.entries(domain)) {
    if (spec && typeof spec === 'object') {
      const s = spec as { min?: number; max?: number; unit?: string };
      entries.push({
        field,
        min: typeof s.min === 'number' ? s.min : null,
        max: typeof s.max === 'number' ? s.max : null,
        unit: typeof s.unit === 'string' ? s.unit : null,
      });
    }
  }
  return entries;
}

export default ModelDetail;
