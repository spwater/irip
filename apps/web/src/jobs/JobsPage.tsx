import { useState } from 'react';
import {
  Button,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
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
import { ActionBar, DataTableShell, StatusMark, FeedbackState } from '@/components/ui';
import {
  JOB_STATUS_VIEW,
  TERMINAL_STATUSES,
  CANCELLABLE_STATUSES,
  jobStatusView,
} from './jobPresentation';

const { Text } = Typography;

/**
 * 作业列表页面
 *
 * 功能：
 * - Table: 作业列表（ID / 类型 / 状态 / 阶段 / 进度 / 创建时间）
 * - 状态和类型筛选
 * - 重试/取消操作
 * - 点击行跳转到作业详情页
 * - 使用共享 JOB_STATUS_VIEW 状态映射
 */
export function JobsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [kindFilter, setKindFilter] = useState<string | undefined>(undefined);

  // ---- 数据查询：作业列表 ----
  const { data, isLoading, isError, refetch } = useQuery({
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
          <span
            className="ocean-tech"
            style={{ cursor: 'pointer', color: '#1686AE' }}
            onClick={() => void navigate({ to: '/jobs/$jobId', params: { jobId: val } })}
          >
            {val.slice(0, 16)}…
          </span>
        </Tooltip>
      ),
    },
    {
      title: '类型',
      dataIndex: 'kind',
      key: 'kind',
      width: 160,
      render: (val: string) => <span className="ocean-tech">{val}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: string) => {
        const view = jobStatusView(status);
        return <StatusMark tone={view.tone} label={view.label} />;
      },
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
      render: (val: string) => (
        <span className="ocean-tabular-number" style={{ fontSize: 12 }}>
          {new Date(val).toLocaleString('zh-CN', { hour12: false })}
        </span>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: unknown, record: JobListItem) => {
        const canCancel = CANCELLABLE_STATUSES.includes(record.status as JobStatus);
        const canRetry = TERMINAL_STATUSES.includes(record.status as JobStatus);
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

  // ---- 工具栏 ----
  const toolbar = (
    <ActionBar
      filters={
        <>
          <Select
            placeholder="状态筛选"
            style={{ width: 160 }}
            value={statusFilter ?? '__all__'}
            onChange={(val: string) => setStatusFilter(val === '__all__' ? undefined : val)}
            options={[
              { value: '__all__', label: '全部' },
              ...(Object.entries(JOB_STATUS_VIEW) as [string, { label: string; tone: string }][]).map(
                ([value, view]) => ({ value, label: view.label }),
              ),
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
        </>
      }
    />
  );

  // ---- 表格内容 ----
  const tableContent: JSX.Element = (() => {
    if (isLoading) {
      return <FeedbackState kind="loading" title="正在加载作业列表..." rows={5} />;
    }
    if (isError && items.length === 0) {
      return (
        <FeedbackState
          kind="error"
          title="作业列表加载失败"
          onRetry={() => void refetch()}
        />
      );
    }
    return (
      <Table<JobListItem>
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={isLoading}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        size="middle"
      />
    );
  })();

  return (
    <section aria-label="作业目录">
      <DataTableShell
        title="作业中心"
        description="查看和管理平台异步作业，支持取消和重试。"
        toolbar={toolbar}
      >
        {tableContent}
      </DataTableShell>
    </section>
  );
}
