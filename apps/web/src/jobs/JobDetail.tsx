import {
  Button,
  Descriptions,
  Popconfirm,
  Progress,
  Space,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import {
  apiCancelJob,
  apiGetJobDetail,
  apiRetryJob,
  extractApiError,
} from '@/api/client';
import { PageIntro, DataHero, DetailSection, StatusMark, FeedbackState } from '@/components/ui';
import {
  TERMINAL_STATUSES,
  CANCELLABLE_STATUSES,
  jobStatusView,
} from './jobPresentation';

const { Text } = Typography;

/** JSON 预览块 — 可选择可复制 */
function JsonPreview({ data, variant }: { data: Record<string, unknown>; variant: 'default' | 'error' | 'success' }): JSX.Element {
  const bg = variant === 'error' ? '#fff2f0' : variant === 'success' ? '#f6ffed' : '#f5f5f5';
  const border = variant === 'error' ? '#ffccc7' : variant === 'success' ? '#b7eb8f' : 'rgba(24, 102, 133, 0.16)';
  return (
    <pre
      className="ocean-tech"
      style={{
        background: bg,
        padding: 12,
        borderRadius: 6,
        fontSize: 12,
        overflow: 'auto',
        maxHeight: 300,
        border: `1px solid ${border}`,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        margin: 0,
      }}
    >
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

/**
 * 作业详情页面
 *
 * 功能：
 * - 作业基本信息（ID / 类型 / 状态 / 阶段 / 进度 / 创建时间）— 命名 region: 作业基本信息
 * - 输入载荷 — 命名 region: 输入载荷
 * - 错误日志 — 命名 region: 错误日志
 * - 执行结果 / 工件 — 命名 region: 执行结果
 * - 重试/取消操作
 * - 使用共享 JOB_STATUS_VIEW 状态映射
 */
export function JobDetail(): JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { jobId } = useParams({ strict: false });

  // ---- 数据查询：作业详情 ----
  const { data, isLoading, isError, refetch, error } = useQuery({
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

  // ---- 加载中 ----
  if (isLoading) {
    return (
      <div>
        <PageIntro index="JOBS / DETAIL" title="作业详情" />
        <FeedbackState kind="loading" title="正在加载作业详情..." rows={6} />
      </div>
    );
  }

  // ---- 查询错误 ----
  if (isError || !data) {
    const errorDetail = error instanceof Error ? error.message : '作业详情获取失败';
    return (
      <div>
        <PageIntro index="JOBS / DETAIL" title="作业详情" />
        <FeedbackState
          kind="error"
          title="作业详情获取失败"
          description={errorDetail}
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  const job = data;
  const canCancel = CANCELLABLE_STATUSES.includes(job.status);
  const canRetry = TERMINAL_STATUSES.includes(job.status);
  const view = jobStatusView(job.status);

  return (
    <div>
      <PageIntro
        index="JOBS / DETAIL"
        title="作业详情"
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
                  重试作业
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

      {/* 状态英雄区 */}
      <DataHero
        label="作业状态"
        value={view.label}
        summary={<StatusMark tone={view.tone} label={view.label} />}
      />

      {/* 作业基本信息 */}
      <DetailSection title="作业基本信息">
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="作业 ID">
            <span className="ocean-tech">{job.id}</span>
          </Descriptions.Item>
          <Descriptions.Item label="类型">
            <span className="ocean-tech">{job.kind}</span>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <StatusMark tone={view.tone} label={view.label} />
          </Descriptions.Item>
          <Descriptions.Item label="阶段">
            {job.stage || <Text type="secondary">-</Text>}
          </Descriptions.Item>
          <Descriptions.Item label="进度">
            <Progress percent={job.progress} size="small" style={{ width: 200 }} />
          </Descriptions.Item>
          <Descriptions.Item label="可重试">
            {job.retryable ? <StatusMark tone="warning" label="是" /> : <StatusMark tone="neutral" label="否" />}
          </Descriptions.Item>
          <Descriptions.Item label="尝试次数">
            <span className="ocean-tabular-number">{job.attempt} / {job.max_attempts}</span>
          </Descriptions.Item>
          <Descriptions.Item label="创建者">
            {job.created_by ? <span className="ocean-tech">{job.created_by}</span> : <Text type="secondary">-</Text>}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            <span className="ocean-tabular-number">{new Date(job.created_at).toLocaleString('zh-CN', { hour12: false })}</span>
          </Descriptions.Item>
          <Descriptions.Item label="更新时间">
            <span className="ocean-tabular-number">{new Date(job.updated_at).toLocaleString('zh-CN', { hour12: false })}</span>
          </Descriptions.Item>
        </Descriptions>
      </DetailSection>

      {/* 输入载荷 */}
      {job.payload && Object.keys(job.payload).length > 0 && (
        <DetailSection title="输入载荷" technical>
          <JsonPreview data={job.payload} variant="default" />
        </DetailSection>
      )}

      {/* 错误日志 */}
      {job.last_error && (
        <DetailSection title="错误日志" technical>
          <JsonPreview data={job.last_error} variant="error" />
        </DetailSection>
      )}

      {/* 执行结果 */}
      {job.result && Object.keys(job.result).length > 0 && (
        <DetailSection title="执行结果" technical>
          <JsonPreview data={job.result} variant="success" />
          {/* 工件链接 */}
          {Boolean(job.result.artifact_id) && (
            <div style={{ marginTop: 12 }}>
              <Space>
                <Text>工件 ID: </Text>
                <Text copyable className="ocean-tech">
                  {String(job.result.artifact_id)}
                </Text>
              </Space>
            </div>
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

      {/* 终态提示 — 无错误和结果时显示执行历史 */}
      {TERMINAL_STATUSES.includes(job.status) && !job.last_error && !job.result && (
        <DetailSection title="执行历史">
          <Descriptions column={1} size="small">
            <Descriptions.Item label="当前状态">
              <StatusMark tone={view.tone} label={view.label} />
            </Descriptions.Item>
            <Descriptions.Item label="尝试次数">
              <span className="ocean-tabular-number">{job.attempt} / {job.max_attempts}</span>
            </Descriptions.Item>
            <Descriptions.Item label="完成时间">
              <span className="ocean-tabular-number">{new Date(job.updated_at).toLocaleString('zh-CN', { hour12: false })}</span>
            </Descriptions.Item>
          </Descriptions>
        </DetailSection>
      )}
    </div>
  );
}
