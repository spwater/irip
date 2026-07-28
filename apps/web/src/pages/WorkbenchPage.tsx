/**
 * 研发看板页面（Data Ocean Phase 2）
 *
 * 使用 5 个独立 TanStack Query 查询，每个面板独立管理自身的 loading/error/success 状态。
 * 不虚构总数、趋势、百分比或事件时间戳；仅在 API 返回真实数据时展示。
 * 分页或受限数据以 "最近作业" / "当前返回" 等明确范围标注。
 */
import { Button, Space, Table, Typography } from 'antd';
import { useNavigate } from '@tanstack/react-router';
import type { ColumnsType } from 'antd/es/table';
import { useWorkbenchSummary } from '@/pages/useWorkbenchSummary';
import {
  DataHero,
  FeedbackState,
  MetricStrip,
  OceanPanel,
  PageIntro,
  StatusMark,
} from '@/components/ui';
import type { JobListItem } from '@/api/client';

const { Text } = Typography;

/** 作业状态 → StatusMark tone 映射 */
const JOB_STATUS_TONE: Record<string, 'success' | 'info' | 'warning' | 'danger' | 'neutral'> = {
  succeeded: 'success',
  running: 'info',
  pending: 'neutral',
  failed: 'danger',
  cancelled: 'neutral',
  retrying: 'warning',
};

/** 作业状态 → 中文标签 */
const JOB_STATUS_LABEL: Record<string, string> = {
  succeeded: '成功',
  running: '运行中',
  pending: '等待中',
  failed: '失败',
  cancelled: '已取消',
  retrying: '重试中',
};

/**
 * 研发看板 — 真实数据概览页面。
 *
 * 面板结构（每个面板独立读取自己的 query 结果）：
 * 1. PageIntro — 页面引导
 * 2. DataHero — 任务内事实计数（来自 facts.group_counts）
 * 3. MetricStrip — 流程数 / 模型数 / 最近作业数（各自成功时展示）
 * 4. 最近作业表 — 来自 jobs 查询（limit=8，标注 "最近作业"）
 * 5. 系统健康摘要 — 来自 health 查询
 * 6. 路由链接 — 跳转到各功能页面
 */
export function WorkbenchPage(): JSX.Element {
  const summary = useWorkbenchSummary();
  const { facts, flows, models, jobs, health, factCount } = summary;
  const navigate = useNavigate();

  // ── 最近作业表列定义 ──
  const jobColumns: ColumnsType<JobListItem> = [
    {
      title: '作业',
      dataIndex: 'kind',
      key: 'kind',
      width: 220,
      render: (kind: string) => <Text code>{kind}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <StatusMark
          tone={JOB_STATUS_TONE[status] ?? 'neutral'}
          label={JOB_STATUS_LABEL[status] ?? status}
        />
      ),
    },
    {
      title: '阶段',
      dataIndex: 'stage',
      key: 'stage',
      width: 180,
      render: (stage: string) => stage || <Text type="secondary">-</Text>,
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      width: 80,
      align: 'center' as const,
      render: (progress: number) => `${progress}%`,
    },
    {
      title: '尝试',
      key: 'attempt',
      width: 80,
      align: 'center' as const,
      render: (_: unknown, record: JobListItem) => `${record.attempt}/${record.max_attempts}`,
    },
  ];

  // ── MetricStrip items（仅收集成功面板的指标）──
  const metricItems = [
    {
      key: 'flows',
      label: '流程',
      value: flows.data?.items?.length ?? 0,
      unit: '个',
      note: flows.data ? '当前返回' : undefined,
    },
    {
      key: 'models',
      label: '模型',
      value: models.data?.items?.length ?? 0,
      unit: '个',
      note: models.data ? '当前返回' : undefined,
    },
    {
      key: 'jobs',
      label: '最近作业',
      value: jobs.data?.items?.length ?? 0,
      unit: '条',
      note: jobs.data ? '最近 8 条' : undefined,
    },
  ];

  // ── 导航链接 ──
  const navLinks = [
    { to: '/standards', label: '实验室建设' },
    { to: '/lab-ops', label: '实验室运营' },
    { to: '/models', label: '模型管理' },
    { to: '/jobs', label: '作业管理' },
    { to: '/governance', label: '平台治理' },
    { to: '/platform', label: '平台应用' },
  ] as const;

  return (
    <div className="ocean-page ocean-workbench">
      <PageIntro
        index="IRIP / 01"
        title="研发看板"
        description="观察正在流动的实验、数据、模型与作业。"
      />

      {/* ── 任务内事实 — DataHero（独立读取 facts query）── */}
      <OceanPanel level="strong" className="ocean-workbench__hero-panel">
        {facts.isLoading ? (
          <FeedbackState kind="loading" title="正在加载任务内事实…" rows={1} />
        ) : facts.isError ? (
          <FeedbackState
            kind="error"
            title="事实加载失败"
            description={facts.error instanceof Error ? facts.error.message : '请稍后重试'}
            onRetry={() => void facts.refetch()}
          />
        ) : (
          <DataHero
            label="任务内事实"
            value={factCount}
            unit="条"
            summary="来自当前事实接口的任务分组统计"
          />
        )}
      </OceanPanel>

      {/* ── 指标条 — 流程 / 模型 / 最近作业（各自独立读取）── */}
      <OceanPanel className="ocean-workbench__metrics">
        {flows.isError || models.isError || jobs.isError ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            {flows.isError ? (
              <FeedbackState
                kind="error"
                title="流程加载失败"
                description={flows.error instanceof Error ? flows.error.message : '请稍后重试'}
                onRetry={() => void flows.refetch()}
              />
            ) : null}
            {models.isError ? (
              <FeedbackState
                kind="error"
                title="模型加载失败"
                description={models.error instanceof Error ? models.error.message : '请稍后重试'}
                onRetry={() => void models.refetch()}
              />
            ) : null}
            {jobs.isError ? (
              <FeedbackState
                kind="error"
                title="作业加载失败"
                description={jobs.error instanceof Error ? jobs.error.message : '请稍后重试'}
                onRetry={() => void jobs.refetch()}
              />
            ) : null}
          </Space>
        ) : flows.isLoading || models.isLoading || jobs.isLoading ? (
          <FeedbackState kind="loading" title="正在加载指标…" rows={1} />
        ) : (
          <MetricStrip items={metricItems} />
        )}
      </OceanPanel>

      {/* ── 最近作业表（独立读取 jobs query）── */}
      <OceanPanel className="ocean-workbench__jobs">
        <div className="ocean-workbench__section-title">最近作业</div>
        {jobs.isLoading ? (
          <FeedbackState kind="loading" title="正在加载最近作业…" rows={3} />
        ) : jobs.isError ? (
          <FeedbackState
            kind="error"
            title="作业加载失败"
            description={jobs.error instanceof Error ? jobs.error.message : '请稍后重试'}
            onRetry={() => void jobs.refetch()}
          />
        ) : (jobs.data?.items?.length ?? 0) === 0 ? (
          <FeedbackState kind="empty" title="暂无最近作业" description="当前没有正在运行或已完成的作业" />
        ) : (
          <Table<JobListItem>
            columns={jobColumns}
            dataSource={jobs.data?.items ?? []}
            rowKey="id"
            pagination={false}
            size="middle"
            scroll={{ x: 660 }}
          />
        )}
      </OceanPanel>

      {/* ── 系统健康摘要（独立读取 health query）── */}
      <OceanPanel>
        <div className="ocean-workbench__section-title">系统健康</div>
        {health.isLoading ? (
          <FeedbackState kind="loading" title="正在检查系统健康…" rows={2} />
        ) : health.isError ? (
          <FeedbackState
            kind="partial"
            title="系统健康检查不可用"
            description={health.error instanceof Error ? health.error.message : '健康接口暂时不可用'}
            onRetry={() => void health.refetch()}
          />
        ) : (
          <div className="ocean-workbench__health-list">
            <div className="ocean-workbench__health-item">
              <span>整体状态</span>
              <StatusMark
                tone={
                  health.data?.status === 'ok'
                    ? 'success'
                    : health.data?.status === 'degraded'
                      ? 'warning'
                      : 'danger'
                }
                label={health.data?.status ?? '未知'}
              />
            </div>
            {health.data?.checks?.map((check) => (
              <div key={check.name} className="ocean-workbench__health-item">
                <span>{check.name}</span>
                <StatusMark
                  tone={check.status === 'ok' ? 'success' : check.status === 'degraded' ? 'warning' : 'danger'}
                  label={check.status}
                  detail={check.latency_ms != null ? `${check.latency_ms}ms` : undefined}
                />
              </div>
            ))}
            {health.data?.migration_version ? (
              <div className="ocean-workbench__health-item">
                <span>迁移版本</span>
                <Text type="secondary">{health.data.migration_version}</Text>
              </div>
            ) : null}
          </div>
        )}
      </OceanPanel>

      {/* ── 路由链接 ── */}
      <OceanPanel>
        <div className="ocean-workbench__section-title">快速导航</div>
        <div className="ocean-workbench__links">
          {navLinks.map((link) => (
            <Button key={link.to} onClick={() => void navigate({ to: link.to })}>
              {link.label}
            </Button>
          ))}
        </div>
      </OceanPanel>
    </div>
  );
}
