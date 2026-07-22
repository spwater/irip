import { useState } from 'react';
import {
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateVariable,
  apiDeprecateVariable,
  apiListVariableVersions,
  apiListVariables,
  apiPublishVariable,
  apiRejectVariable,
  apiResubmitVariable,
  apiSubmitVariable,
  extractApiError,
  type VariableSummary,
  type VariableVersion,
} from '@/api/client';
import { ObjectGraphPage } from '@/objects/ObjectGraphPage';
import { DepartmentManagement } from '@/pages/governance/DepartmentManagement';
import { EquipmentPage } from '@/equipment/EquipmentPage';

const { Title, Text } = Typography;

/** 状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  draft: 'blue',
  in_review: 'orange',
  published: 'green',
  deprecated: 'default',
  rejected: 'red',
};

/** 状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  in_review: '审核中',
  published: '已发布',
  deprecated: '已弃用',
  rejected: '已驳回',
};

/**
 * 标准管理页面
 *
 * 功能：
 * - Ant Design Table 列表（编码 / 中文名 / 英文名 / 量纲 / 数据类型 / 状态 / 当前版本 / 操作）
 * - 顶部「新建变量」按钮 + 状态筛选
 * - Modal + Form 创建弹窗
 * - 基于状态的操作按钮（提交审核 / 发布 / 驳回 / 弃用 / 重新提交）
 * - 版本历史抽屉（Timeline）
 */
export function StandardsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [versionDrawerRecord, setVersionDrawerRecord] = useState<VariableSummary | null>(null);

  // ---- 数据查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['variables', statusFilter],
    queryFn: () => apiListVariables({ status: statusFilter, limit: 50 }),
  });

  const items: VariableSummary[] = data?.items ?? [];

  // ---- 版本历史查询 ----
  const { data: versions } = useQuery({
    queryKey: ['variable-versions', versionDrawerRecord?.id],
    queryFn: () => apiListVariableVersions(versionDrawerRecord!.id),
    enabled: !!versionDrawerRecord,
  });

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateVariable,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['variables'] });
      setModalOpen(false);
      form.resetFields();
      message.success('变量创建成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 状态操作 Mutation ----
  const actionMutation = useMutation({
    mutationFn: async (params: { action: string; variableId: string }) => {
      switch (params.action) {
        case 'submit':
          return apiSubmitVariable(params.variableId);
        case 'publish':
          return apiPublishVariable(params.variableId);
        case 'reject':
          return apiRejectVariable(params.variableId);
        case 'deprecate':
          return apiDeprecateVariable(params.variableId);
        case 'resubmit':
          return apiResubmitVariable(params.variableId);
        default:
          throw new Error(`未知操作: ${params.action}`);
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['variables'] });
      message.success('操作成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 事件处理 ----
  const handleCreate = (): void => {
    form.resetFields();
    setModalOpen(true);
  };

  const handleSubmit = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      createMutation.mutate({
        code: values.code,
        name_zh: values.name_zh,
        name_en: values.name_en,
        quantity_kind: values.quantity_kind,
        data_type: values.data_type,
        unit: values.unit,
        description: values.description,
      });
    } catch {
      // 表单校验失败
    }
  };

  const handleAction = (action: string, record: VariableSummary): void => {
    actionMutation.mutate({ action, variableId: record.id });
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<VariableSummary> = [
    {
      title: '编码',
      dataIndex: 'code',
      key: 'code',
      width: 160,
    },
    {
      title: '中文名',
      dataIndex: 'name_zh',
      key: 'name_zh',
    },
    {
      title: '英文名',
      dataIndex: 'name_en',
      key: 'name_en',
    },
    {
      title: '量纲',
      dataIndex: 'quantity_kind',
      key: 'quantity_kind',
      width: 140,
    },
    {
      title: '数据类型',
      dataIndex: 'data_type',
      key: 'data_type',
      width: 100,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={STATUS_COLOR[status] ?? 'default'}>
          {STATUS_LABEL[status] ?? status}
        </Tag>
      ),
    },
    {
      title: '当前版本',
      dataIndex: 'current_version',
      key: 'current_version',
      width: 100,
      render: (v: string | null) => v ?? '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 280,
      render: (_: unknown, record: VariableSummary) => (
        <Space size="small" wrap>
          {record.status === 'draft' && (
            <Button
              size="small"
              type="link"
              onClick={() => handleAction('submit', record)}
            >
              提交审核
            </Button>
          )}
          {record.status === 'in_review' && (
            <>
              <Button
                size="small"
                type="link"
                onClick={() => handleAction('publish', record)}
              >
                发布
              </Button>
              <Button
                size="small"
                type="link"
                danger
                onClick={() => handleAction('reject', record)}
              >
                驳回
              </Button>
            </>
          )}
          {record.status === 'published' && (
            <Button
              size="small"
              type="link"
              danger
              onClick={() => handleAction('deprecate', record)}
            >
              弃用
            </Button>
          )}
          {record.status === 'rejected' && (
            <Button
              size="small"
              type="link"
              onClick={() => handleAction('resubmit', record)}
            >
              重新提交
            </Button>
          )}
          <Button
            size="small"
            type="link"
            onClick={() => setVersionDrawerRecord(record)}
          >
            版本历史
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Tabs
      defaultActiveKey="variables"
      items={[
        {
          key: 'departments',
          label: '组织机构',
          children: <DepartmentManagement />,
        },
        {
          key: 'equipment',
          label: '设备仪器',
          children: <EquipmentPage />,
        },
        {
          key: 'variables',
          label: '物理量管理',
          children: (
            <div>
              <Space style={{ marginBottom: 16 }}>
                <Button type="primary" onClick={handleCreate}>
                  新建变量
                </Button>
                <Select
                  placeholder="状态筛选"
                  allowClear
                  style={{ width: 140 }}
                  value={statusFilter}
                  onChange={(val: string | undefined) => setStatusFilter(val)}
                  options={[
                    { value: 'draft', label: '草稿' },
                    { value: 'in_review', label: '审核中' },
                    { value: 'published', label: '已发布' },
                    { value: 'deprecated', label: '已弃用' },
                    { value: 'rejected', label: '已驳回' },
                  ]}
                />
              </Space>

              <Table<VariableSummary>
                columns={columns}
                dataSource={items}
                rowKey="id"
                loading={isLoading}
                pagination={{ pageSize: 20, showSizeChanger: false }}
                size="middle"
              />

              {/* 创建变量 Modal */}
              <Modal
                title="新建变量"
                open={modalOpen}
                onOk={handleSubmit}
                onCancel={() => {
                  setModalOpen(false);
                  form.resetFields();
                }}
                confirmLoading={createMutation.isPending}
                okText="保存"
                cancelText="取消"
                width={600}
              >
                <Form form={form} layout="vertical">
                  <Form.Item
                    name="code"
                    label="变量编码"
                    rules={[
                      { required: true, message: '请输入变量编码' },
                      {
                        pattern: /^[a-z][a-z0-9_]*$/,
                        message: '仅小写字母/数字/下划线，首字符必须为字母',
                      },
                    ]}
                  >
                    <Input placeholder="如：temperature" maxLength={64} />
                  </Form.Item>
                  <Form.Item
                    name="name_zh"
                    label="中文名"
                    rules={[{ required: true, message: '请输入中文名' }]}
                  >
                    <Input placeholder="如：温度" maxLength={200} />
                  </Form.Item>
                  <Form.Item
                    name="name_en"
                    label="英文名"
                    rules={[{ required: true, message: '请输入英文名' }]}
                  >
                    <Input placeholder="如：temperature" maxLength={200} />
                  </Form.Item>
                  <Form.Item
                    name="quantity_kind"
                    label="量纲"
                    rules={[{ required: true, message: '请输入量纲' }]}
                  >
                    <Input placeholder="如：thermodynamic_temperature" />
                  </Form.Item>
                  <Form.Item
                    name="data_type"
                    label="数据类型"
                    rules={[{ required: true, message: '请选择数据类型' }]}
                  >
                    <Select
                      placeholder="选择数据类型"
                      options={[
                        { value: 'number', label: '数值' },
                        { value: 'string', label: '字符串' },
                        { value: 'boolean', label: '布尔' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="unit" label="单位">
                    <Input placeholder="如：℃" />
                  </Form.Item>
                  <Form.Item name="description" label="描述">
                    <Input.TextArea placeholder="变量描述（可选）" rows={3} />
                  </Form.Item>
                </Form>
              </Modal>

              {/* 版本历史 Drawer */}
              <Drawer
                title="版本历史"
                open={!!versionDrawerRecord}
                onClose={() => setVersionDrawerRecord(null)}
                width={500}
              >
                {versionDrawerRecord && (
                  <>
                    <Title level={5}>
                      {versionDrawerRecord.name_zh}（{versionDrawerRecord.code}）
                    </Title>
                    <Timeline
                      items={(versions ?? []).map((v: VariableVersion) => ({
                        color:
                          v.status === 'published'
                            ? 'green'
                            : v.status === 'rejected'
                              ? 'red'
                              : 'blue',
                        children: (
                          <div>
                            <Text strong>版本 {v.version}</Text>
                            <br />
                            <Tag color={STATUS_COLOR[v.status] ?? 'default'}>
                              {STATUS_LABEL[v.status] ?? v.status}
                            </Tag>
                            <br />
                            <Text type="secondary">{v.created_at} by {v.created_by}</Text>
                            {v.change_note && (
                              <>
                                <br />
                                <Text type="secondary">{v.change_note}</Text>
                              </>
                            )}
                          </div>
                        ),
                      }))}
                    />
                  </>
                )}
              </Drawer>
            </div>
          ),
        },
        {
          key: 'objects',
          label: '对象图谱',
          children: <ObjectGraphPage />,
        },
      ]}
    />
  );
}
