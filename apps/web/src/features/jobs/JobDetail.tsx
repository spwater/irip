import {
  Button,
  Descriptions,
  Popconfirm,
  Progress,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import { apiCancelJob } from '@/api/client';
import { apiGetJobDetail, apiRetryJob } from '@/api/governance';
import { extractApiError } from '@/api/types';
import { PageIntro, DetailSection, StatusMark } from '@/shared/ui';
import { QueryStateDisplay } from '@/features/components/StateDisplay';
import type { StatusSemantic } from '@/theme/tokens';

const { Text, Paragraph } = Typography;

/** 状态 → 语义映射（用于 StatusMark） */
const JOB_SEMANTIC: Record<string, StatusSemantic> = {
  accepted: 'neutral',
  queued: 'info',
  running: 'info',
  retry_wait: 'warning',
  succeeded: 'success',
  failed: 'danger',
  cancel_requested: 'warning',
  cancelled: 'neutral',
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
 * 作业详情页面
 *
 * 功能：
 * - 作业基本信息（ID / 类型 / 状态 / 阶段 / 进度 / 创建时间）
 * - 执行历史/尝试（attempt / max_attempts）
 * - 日志展示（last_error）
 * - 工件链接（result）
 * - 重试/取消操作
 */
export function JobDetail(): JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { jobId } = useParams({ strict: false });

  // ---- 数据查询：作业详情 ----
  const { data, isError, error, refetch } = useQuery({
    queryKey: ['jobs', 'detail', jobId],
    queryFn: () => apiGetJobDetail(jobId as string),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      // 非终态时每 3 秒刷新
      return status && !TERMINAL_STATUSES.includes(status) ? 3000 : false;
    },
  });

  // ---- 取消 Mutation ----
  const cancelMutation = useMutation({
    mutationFn: () => apiCancelJob(jobId as string),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs', 'detail', jobId] });
      void queryClient.invalidateQueries({ queryKey: ['jobs', 'list'] });
      message.success('取消请求已发送');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 重试 Mutation ----
  const retryMutation = useMutation({
    mutationFn: () => apiRetryJob(jobId as string),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['jobs', 'list'] });
      message.success(`重试作业已创建（ID: ${result.id}）`);
      void navigate({ to: '/jobs/$jobId', params: { jobId: result.id } });
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // 数据未就绪：优先展示错误，其次 loading
  if (!data) {
    if (isError) {
      return (
        <div className="ocean-page-enter">
          <PageIntro
            index="DETAIL / JOB"
            title="作业详情"
            subtitle="作业基本信息、执行历史与工件。"
            actions={
              <Button onClick={() => void navigate({ to: '/jobs' })}>返回列表</Button>
            }
          />
          <QueryStateDisplay
            isLoading={false}
            isError={isError}
            error={error}
            onRetry={() => void refetch()}
            loadingTitle="加载作业详情…"
            style={{ padding: 48 }}
          >
            <span />
          </QueryStateDisplay>
        </div>
      );
    }
    return (
      <QueryStateDisplay
        isLoading={true}
        isError={false}
        error={null}
        loadingTitle="加载作业详情…"
        style={{ padding: 48 }}
      >
        <span />
      </QueryStateDisplay>
    );
  }

  const job = data;
  const canCancel = CANCELLABLE_STATUSES.includes(job.status);
  const canRetry = TERMINAL_STATUSES.includes(job.status);

  return (
    <div className="ocean-page-enter">
      <PageIntro
        index="DETAIL / JOB"
        title="作业详情"
        subtitle="作业基本信息、执行历史与工件。"
        actions={
          <Space>
            <Button onClick={() => void navigate({ to: '/jobs' })}>返回列表</Button>
            {canRetry && (
              <Popconfirm
                title="确定重试此作业？"
                onConfirm={() => retryMutation.mutate()}
                okText="确定"
                cancelText="取消"
              >
                <Button type="primary" loading={retryMutation.isPending}>
                  重试
                </Button>
              </Popconfirm>
            )}
            {canCancel && (
              <Popconfirm
                title="确定取消此作业？"
                onConfirm={() => cancelMutation.mutate()}
                okText="确定"
                cancelText="取消"
              >
                <Button danger loading={cancelMutation.isPending}>
                  取消作业
                </Button>
              </Popconfirm>
            )}
          </Space>
        }
      />

      {/* 基本信息 */}
      <DetailSection title="基本信息" style={{ marginBottom: 16 }}>
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="作业 ID">{job.id}</Descriptions.Item>
          <Descriptions.Item label="类型">{job.kind}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <StatusMark
              semantic={JOB_SEMANTIC[job.status] ?? 'neutral'}
              label={STATUS_LABEL[job.status] ?? job.status}
            />
          </Descriptions.Item>
          <Descriptions.Item label="阶段">
            {job.stage || <Text type="secondary">-</Text>}
          </Descriptions.Item>
          <Descriptions.Item label="进度">
            <Progress percent={job.progress} size="small" style={{ width: 200 }} />
          </Descriptions.Item>
          <Descriptions.Item label="可重试">
            {job.retryable ? <Tag color="orange">是</Tag> : <Tag>否</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="尝试次数">
            {job.attempt} / {job.max_attempts}
          </Descriptions.Item>
          <Descriptions.Item label="创建者">
            {job.created_by_name ?? job.created_by ?? <Text type="secondary">-</Text>}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {new Date(job.created_at).toLocaleString()}
          </Descriptions.Item>
          <Descriptions.Item label="更新时间">
            {new Date(job.updated_at).toLocaleString()}
          </Descriptions.Item>
        </Descriptions>
      </DetailSection>

      {/* 输入载荷 */}
      {job.payload && Object.keys(job.payload).length > 0 && (
        <DetailSection title="输入载荷" style={{ marginBottom: 16 }}>
          <Paragraph>
            <pre
              style={{
                background: 'var(--ocean-surface-structural)',
                padding: 12,
                borderRadius: 4,
                fontSize: 12,
                fontFamily: 'var(--ocean-font-mono)',
                overflow: 'auto',
                maxHeight: 300,
              }}
            >
              {JSON.stringify(job.payload, null, 2)}
            </pre>
          </Paragraph>
        </DetailSection>
      )}

      {/* 错误日志 */}
      {job.last_error && (
        <DetailSection title="错误日志" style={{ marginBottom: 16 }}>
          <Paragraph>
            <pre
              style={{
                background: 'rgba(165, 61, 82, 0.08)',
                padding: 12,
                borderRadius: 4,
                fontSize: 12,
                fontFamily: 'var(--ocean-font-mono)',
                overflow: 'auto',
                maxHeight: 300,
                border: '1px solid var(--ocean-border-strong)',
              }}
            >
              {JSON.stringify(job.last_error, null, 2)}
            </pre>
          </Paragraph>
        </DetailSection>
      )}

      {/* 执行结果 / 工件 */}
      {job.result && Object.keys(job.result).length > 0 && (
        <DetailSection title="执行结果 / 工件" style={{ marginBottom: 16 }}>
          <Paragraph>
            <pre
              style={{
                background: 'rgba(20, 118, 94, 0.06)',
                padding: 12,
                borderRadius: 4,
                fontSize: 12,
                fontFamily: 'var(--ocean-font-mono)',
                overflow: 'auto',
                maxHeight: 400,
                border: '1px solid var(--ocean-border-subtle)',
              }}
            >
              {JSON.stringify(job.result, null, 2)}
            </pre>
          </Paragraph>
          {/* 工件链接：如果 result 中包含 artifact_id 或 download_url，显示链接 */}
          {Boolean(job.result.artifact_id) && (
            <Space>
              <Text>工件 ID: </Text>
              <Text copyable style={{ fontFamily: 'var(--ocean-font-mono)' }}>
                {String(job.result.artifact_id)}
              </Text>
            </Space>
          )}
          {Boolean(job.result.download_url) && (
            <div style={{ marginTop: 8 }}>
              <a href={String(job.result.download_url)} target="_blank" rel="noreferrer">
                下载工件
              </a>
            </div>
          )}
        </DetailSection>
      )}

      {/* 终态提示 */}
      {TERMINAL_STATUSES.includes(job.status) && !job.last_error && !job.result && (
        <DetailSection title="执行历史">
          <Descriptions column={1} size="small">
            <Descriptions.Item label="当前状态">
              <StatusMark
                semantic={JOB_SEMANTIC[job.status] ?? 'neutral'}
                label={STATUS_LABEL[job.status] ?? job.status}
              />
            </Descriptions.Item>
            <Descriptions.Item label="尝试次数">
              {job.attempt} / {job.max_attempts}
            </Descriptions.Item>
            <Descriptions.Item label="完成时间">
              {new Date(job.updated_at).toLocaleString()}
            </Descriptions.Item>
          </Descriptions>
        </DetailSection>
      )}
    </div>
  );
}
