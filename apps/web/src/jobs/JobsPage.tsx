import { useState } from 'react';
import {
  Button,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCancelJob,
  apiListJobs,
  apiRetryJob,
  extractApiError,
  type JobListItem,
  type JobStatus,
} from '@/api/client';

const { Text } = Typography;

/** 状态 → 颜色映射 */
const STATUS_COLOR: Record<string, string> = {
  accepted: 'default',
  queued: 'blue',
  running: 'processing',
  retry_wait: 'orange',
  succeeded: 'success',
  failed: 'error',
  cancel_requested: 'warning',
  cancelled: 'default',
};

/** 状态 → 中文标签映射 */
const STATUS_LABEL: Record<string, string> = {
  accepted: '已接受',
  queued: '排队中',
  running: '运行中',
  retry_wait: '等待重试',
  succeeded: '已完成',
  failed: '已失败',
  cancel_requested: '取消请求中',
  cancelled: '已取消',
};

/** 终态集合 */
const TERMINAL_STATUSES: string[] = ['succeeded', 'failed', 'cancelled'];

/** 可取消状态集合 */
const CANCELLABLE_STATUSES: string[] = [
  'accepted',
  'queued',
  'running',
  'retry_wait',
];

/**
 * 作业列表页面
 *
 * 功能：
 * - Table: 作业列表（ID / 类型 / 状态 / 阶段 / 进度 / 创建时间）
 * - 状态和类型筛选
 * - 重试/取消操作
 * - 点击行跳转到作业详情页
 */
export function JobsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [kindFilter, setKindFilter] = useState<string | undefined>(undefined);

  // ---- 数据查询：作业列表 ----
  const { data, isLoading } = useQuery({
    queryKey: ['jobs', 'list', statusFilter, kindFilter],
    queryFn: () =>
      apiListJobs({ status: statusFilter, kind: kindFilter, limit: 100 }),
  });

  const items: JobListItem[] = data?.items ?? [];

  // ---- 取消 Mutation ----
  const cancelMutation = useMutation({
    mutationFn: (id: string) => apiCancelJob(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs', 'list'] });
      message.success('取消请求已发送');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 重试 Mutation ----
  const retryMutation = useMutation({
    mutationFn: (id: string) => apiRetryJob(id),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['jobs', 'list'] });
      message.success(`重试作业已创建（ID: ${result.id}）`);
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 表格列定义 ----
  const columns: ColumnsType<JobListItem> = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 280,
      ellipsis: true,
      render: (val: string) => (
        <Tooltip title={val}>
          <Text
            style={{ fontSize: 12, cursor: 'pointer', color: '#1677ff' }}
            onClick={() => void navigate({ to: '/jobs/$jobId', params: { jobId: val } })}
          >
            {val.slice(0, 16)}…
          </Text>
        </Tooltip>
      ),
    },
    {
      title: '类型',
      dataIndex: 'kind',
      key: 'kind',
      width: 160,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: string) => (
        <Tag color={STATUS_COLOR[status] ?? 'default'}>
          {STATUS_LABEL[status as JobStatus] ?? status}
        </Tag>
      ),
    },
    {
      title: '阶段',
      dataIndex: 'stage',
      key: 'stage',
      width: 200,
      render: (val: string) => val || <Text type="secondary">-</Text>,
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      width: 120,
      render: (progress: number) => <Progress percent={progress} size="small" />,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (val: string) => new Date(val).toLocaleString(),
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: unknown, record: JobListItem) => {
        const canCancel = CANCELLABLE_STATUSES.includes(record.status);
        const canRetry = TERMINAL_STATUSES.includes(record.status);
        return (
          <Space size="small">
            <Button
              type="link"
              size="small"
              onClick={() =>
                void navigate({
                  to: '/jobs/$jobId',
                  params: { jobId: record.id },
                })
              }
            >
              详情
            </Button>
            {canRetry && (
              <Popconfirm
                title="确定重试此作业？"
                onConfirm={() => retryMutation.mutate(record.id)}
                okText="确定"
                cancelText="取消"
              >
                <Button type="link" size="small">
                  重试
                </Button>
              </Popconfirm>
            )}
            {canCancel && (
              <Popconfirm
                title="确定取消此作业？"
                onConfirm={() => cancelMutation.mutate(record.id)}
                okText="确定"
                cancelText="取消"
              >
                <Button type="link" size="small" danger>
                  取消
                </Button>
              </Popconfirm>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder="状态筛选"
          style={{ width: 160 }}
          value={statusFilter ?? '__all__'}
          onChange={(val: string) => setStatusFilter(val === '__all__' ? undefined : val)}
          options={[
            { value: '__all__', label: '全部' },
            ...Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label })),
          ]}
        />
        <Select
          placeholder="类型筛选"
          style={{ width: 200 }}
          value={kindFilter ?? '__all__'}
          onChange={(val: string) => setKindFilter(val === '__all__' ? undefined : val)}
          options={[
            { value: '__all__', label: '全部' },
            { value: 'echo', label: 'echo' },
            { value: 'parse_excel', label: 'parse_excel' },
            { value: 'audit_export', label: 'audit_export' },
            { value: 'ingestion', label: 'ingestion' },
            { value: 'derivation', label: 'derivation' },
          ]}
        />
      </Space>

      <Table<JobListItem>
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={isLoading}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        size="middle"
      />
    </div>
  );
}
