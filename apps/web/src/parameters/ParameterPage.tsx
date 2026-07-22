import { useState } from 'react';
import {
  Button,
  Divider,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
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
  apiCreateParameter,
  apiDeprecateParameter,
  apiListCandidates,
  apiListParameterVersions,
  apiListParameters,
  extractApiError,
  type ParameterCandidate,
  type ParameterSummary,
  type ParameterVersion,
} from '@/api/client';
import { useAuthStore } from '@/auth/AuthProvider';
import { ApprovalPanel } from '@/parameters/ApprovalPanel';
import { ProvenancePage } from '@/provenance/ProvenancePage';

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
 * 参数管理页面
 *
 * 功能：
 * - Ant Design Table 列表（编码 / 中文名 / 状态 / 当前版本 / 证据数 / 陈旧度 / 操作）
 * - 顶部「新建参数」按钮 + 状态筛选
 * - Modal + Form 创建弹窗
 * - 版本历史抽屉（Timeline）
 * - 候选版本抽屉（ApprovalPanel）
 * - 弃用操作（Popconfirm）
 */
export function ParameterPage(): JSX.Element {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [versionsDrawerParam, setVersionsDrawerParam] = useState<ParameterSummary | null>(null);
  const [candidatesDrawerParam, setCandidatesDrawerParam] = useState<ParameterSummary | null>(null);

  // ---- 数据查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['parameters', statusFilter],
    queryFn: () => apiListParameters({ status: statusFilter, limit: 50 }),
  });

  const items: ParameterSummary[] = data?.items ?? [];

  // ---- 版本历史查询 ----
  const { data: versions } = useQuery({
    queryKey: ['parameter-versions', versionsDrawerParam?.id],
    queryFn: () => apiListParameterVersions(versionsDrawerParam!.id),
    enabled: !!versionsDrawerParam,
  });

  // ---- 候选版本查询 ----
  const { data: candidates } = useQuery({
    queryKey: ['candidates', candidatesDrawerParam?.id],
    queryFn: () => apiListCandidates(candidatesDrawerParam!.id),
    enabled: !!candidatesDrawerParam,
  });

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateParameter,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['parameters'] });
      setModalOpen(false);
      form.resetFields();
      message.success('参数创建成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 弃用 Mutation ----
  const deprecateMutation = useMutation({
    mutationFn: apiDeprecateParameter,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['parameters'] });
      message.success('参数已弃用');
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
        unit: values.unit,
        description: values.description,
      });
    } catch {
      // 表单校验失败
    }
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<ParameterSummary> = [
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
      title: '证据数',
      dataIndex: 'evidence_count',
      key: 'evidence_count',
      width: 80,
      align: 'center' as const,
    },
    {
      title: '陈旧度',
      dataIndex: 'staleness_status',
      key: 'staleness_status',
      width: 100,
      render: (s: string | null) =>
        s ? <Tag color="orange">{s}</Tag> : <Tag>正常</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_: unknown, record: ParameterSummary) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => setVersionsDrawerParam(record)}
          >
            版本
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => setCandidatesDrawerParam(record)}
          >
            候选
          </Button>
          {record.status === 'published' && (
            <Popconfirm
              title="确定弃用该参数？"
              onConfirm={() => deprecateMutation.mutate(record.id)}
              okText="确定"
              cancelText="取消"
            >
              <Button type="link" size="small" danger>
                弃用
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={2}>参数管理</Title>
      <Tabs
        defaultActiveKey="list"
        items={[
          {
            key: 'list',
            label: '参数列表',
            children: (
              <>
                <Space style={{ marginBottom: 16 }}>
                  <Button type="primary" onClick={handleCreate}>
                    新建参数
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

                <Table<ParameterSummary>
                  columns={columns}
                  dataSource={items}
                  rowKey="id"
                  loading={isLoading}
                  pagination={{ pageSize: 20, showSizeChanger: false }}
                  size="middle"
                />

                {/* 创建参数 Modal */}
                <Modal
                  title="新建参数"
                  open={modalOpen}
                  onOk={handleSubmit}
                  onCancel={() => {
                    setModalOpen(false);
                    form.resetFields();
                  }}
                  confirmLoading={createMutation.isPending}
                  okText="保存"
                  cancelText="取消"
                >
                  <Form form={form} layout="vertical">
                    <Form.Item
                      name="code"
                      label="参数编码"
                      rules={[
                        { required: true, message: '请输入参数编码' },
                        {
                          pattern: /^[a-z][a-z0-9_]*$/,
                          message: '仅小写字母/数字/下划线，首字符必须为字母',
                        },
                      ]}
                    >
                      <Input placeholder="如：yield_strength" maxLength={64} />
                    </Form.Item>
                    <Form.Item
                      name="name_zh"
                      label="中文名"
                      rules={[
                        { required: true, message: '请输入中文名' },
                        { max: 200, message: '名称不超过 200 字符' },
                      ]}
                    >
                      <Input placeholder="如：屈服强度" maxLength={200} />
                    </Form.Item>
                    <Form.Item name="unit" label="单位">
                      <Input placeholder="如：MPa" />
                    </Form.Item>
                    <Form.Item name="description" label="描述">
                      <Input.TextArea placeholder="参数描述（可选）" rows={3} />
                    </Form.Item>
                  </Form>
                </Modal>

                {/* 版本历史 Drawer */}
                <Drawer
                  title="版本历史"
                  open={!!versionsDrawerParam}
                  onClose={() => setVersionsDrawerParam(null)}
                  width={500}
                >
                  {versionsDrawerParam && (
                    <>
                      <Title level={5}>{versionsDrawerParam.name_zh}（{versionsDrawerParam.code}）</Title>
                      <Timeline
                        items={(versions ?? []).map((v: ParameterVersion) => ({
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
                              <Text type="secondary">{v.value} {v.unit}</Text>
                              <br />
                              <Text type="secondary">{v.created_at} by {v.created_by}</Text>
                              <br />
                              <Tag color={STATUS_COLOR[v.status] ?? 'default'}>
                                {STATUS_LABEL[v.status] ?? v.status}
                              </Tag>
                            </div>
                          ),
                        }))}
                      />
                    </>
                  )}
                </Drawer>

                {/* 候选版本 Drawer */}
                <Drawer
                  title="候选版本审批"
                  open={!!candidatesDrawerParam}
                  onClose={() => setCandidatesDrawerParam(null)}
                  width={640}
                >
                  {candidatesDrawerParam && user && (
                    <>
                      <Title level={5}>
                        {candidatesDrawerParam.name_zh}（{candidatesDrawerParam.code}）
                      </Title>
                      {(candidates ?? []).map((c: ParameterCandidate) => (
                        <div key={c.id}>
                          <ApprovalPanel
                            candidate={c}
                            currentUser={user}
                            parameterId={candidatesDrawerParam.id}
                          />
                          <Divider />
                        </div>
                      ))}
                      {(candidates ?? []).length === 0 && (
                        <Text type="secondary">暂无候选版本</Text>
                      )}
                    </>
                  )}
                </Drawer>
              </>
            ),
          },
          {
            key: 'provenance',
            label: '溯源链路',
            children: <ProvenancePage />,
          },
        ]}
      />
    </div>
  );
}
