import { useMemo } from 'react';
import { Button, Table, Typography } from 'antd';
import { useNavigate } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { apiListFacts } from '@/api/facts';
import { apiListFlows } from '@/api/flows';
import { apiListJobs } from '@/api/jobs';
import type { JobListItem } from '@/api/governance';
import { apiGetSystemHealth, type SystemHealth } from '@/api/system';
import { PageIntro, DataHero, MetricStrip, OceanPanel, FeedbackState, StatusMark } from '@/components/ui';
import type { StatusSemantic } from '@/theme/tokens';

const { Text } = Typography;

/** 作业状态 → 语义映射（用于 StatusMark） */
const JOB_STATUS_SEMANTIC: Record<string, StatusSemantic> = {
  accepted: 'neutral',
  queued: 'info',
  running: 'info',
  retry_wait: 'warning',
  succeeded: 'success',
  failed: 'danger',
  cancel_requested: 'warning',
  cancelled: 'neutral',
};

/** 作业状态 → 中文标签 */
const JOB_STATUS_LABEL: Record<string, string> = {
  accepted: '已接受',
  queued: '排队中',
  running: '运行中',
  retry_wait: '等待重试',
  succeeded: '已完成',
  failed: '已失败',
  cancel_requested: '取消请求中',
  cancelled: '已取消',
};

/** 系统健康状态 → 语义映射 */
const HEALTH_SEMANTIC: Record<string, StatusSemantic> = {
  healthy: 'success',
  ok: 'success',
  degraded: 'warning',
  unhealthy: 'danger',
  error: 'danger',
};

/** 快捷入口配置 */
const QUICK_ENTRIES: { label: string; desc: string; to: string; search?: Record<string, string> }[] = [
  { label: '实验室建设', desc: '组织机构 / 设备 / 实验对象', to: '/standards' },
  { label: '实验执行', desc: '流程编排与运行', to: '/lab-ops', search: { tab: 'flows' } },
  { label: '实验记录', desc: '原始数据浏览', to: '/lab-ops', search: { tab: 'facts' } },
  { label: 'AI 助手', desc: '对话与数据查询', to: '/platform', search: { tab: 'assistant' } },
  { label: '作业中心', desc: '作业追踪与重试', to: '/jobs' },
];

/**
 * 研发看板页面 — 真实数据总览
 *
 * 按设计文档第 10.3 节：
 * 1. 平台态势 DataHero
 * 2. MetricStrip 摘要指标（明确统计口径）
 * 3. 活跃作业列表
 * 4. 系统健康摘要
 * 5. 快捷入口
 *
 * 每个查询独立加载、独立错误处理，一个失败不阻断整页。
 * 不伪造统计值，使用"最近记录"明确口径。
 */
export function WorkbenchPage(): JSX.Element {
  const navigate = useNavigate();

  // ---- 独立查询 1：事实列表（最近记录） ----
  const { data: factsData } = useQuery({
    queryKey: ['workbench', 'facts'],
    queryFn: () => apiListFacts({ page_size: 100 }),
  });
  const factsItems = factsData?.items ?? [];
  const factsCount = factsItems.length;

  // ---- 独立查询 2：流程列表 ----
  const { data: flowsData } = useQuery({
    queryKey: ['workbench', 'flows'],
    queryFn: () => apiListFlows(),
  });
  const flowsItems = flowsData?.items ?? [];
  const flowsCount = flowsItems.length;
  const activeFlows = flowsItems.filter((f) => f.status === 'published').length;

  // ---- 独立查询 3：作业列表（最近记录） ----
  const { data: jobsData, isLoading: jobsLoading, error: jobsError } = useQuery({
    queryKey: ['workbench', 'jobs'],
    queryFn: () => apiListJobs({ limit: 50 }),
  });
  const jobsItems = jobsData?.items ?? [];
  const recentJobs = jobsItems.slice(0, 8);
  const activeJobCount = jobsItems.filter(
    (j) => ['accepted', 'queued', 'running', 'retry_wait'].includes(j.status),
  ).length;

  // ---- 独立查询 5：系统健康 ----
  const { data: healthData, isLoading: healthLoading, error: healthError } = useQuery({
    queryKey: ['workbench', 'health'],
    queryFn: () => apiGetSystemHealth(),
    // 503 仍展示返回的健康详情，apiGetSystemHealth 已处理
    retry: false,
  });
  const health: SystemHealth | undefined = healthData;

  // 最近作业表格列
  const jobColumns: ColumnsType<JobListItem> = useMemo(
    () => [
      {
        title: '项目名称',
        dataIndex: 'flow_name',
        key: 'flow_name',
        width: 200,
        ellipsis: true,
        render: (v: string) => v || <Text type="secondary">-</Text>,
      },
      {
        title: '部门',
        dataIndex: 'dept_name',
        key: 'dept_name',
        width: 120,
        ellipsis: true,
        render: (v: string) => v || <Text type="secondary">-</Text>,
      },
      {
        title: '作业 ID',
        dataIndex: 'id',
        key: 'id',
        width: 200,
        ellipsis: true,
        render: (v: string) => (
          <span style={{ fontFamily: 'var(--ocean-font-mono)', fontSize: 12 }}>{v}</span>
        ),
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 110,
        render: (s: string) => (
          <StatusMark
            semantic={JOB_STATUS_SEMANTIC[s] ?? 'neutral'}
            label={JOB_STATUS_LABEL[s] ?? s}
          />
        ),
      },
    ],
    [],
  );

  // MetricStrip 指标（明确统计口径）
  const metrics = [
    { label: '事实记录（最近返回）', value: factsCount, unit: '条' },
    { label: '流程（已发布）', value: activeFlows, unit: `/${flowsCount}` },
    { label: '活跃作业', value: activeJobCount, unit: '个' },
  ];

  return (
    <div className="ocean-page-enter">
      {/* 页面标题区 */}
      <PageIntro
        index="MODULE 01 / RESEARCH WORKBENCH"
        title="研发看板"
        actions={
          <Button type="primary" onClick={() => void navigate({ to: '/lab-ops' })}>
            进入实验
          </Button>
        }
      >
        {/* 平台态势 DataHero：活跃作业数 */}
        <DataHero
          value={activeJobCount}
          label="当前活跃作业"
          unit="个"
          status={activeJobCount > 0 ? 'info' : 'neutral'}
        />
      </PageIntro>

      {/* 摘要指标条 */}
      <div style={{ marginBottom: 24 }}>
        <MetricStrip metrics={metrics} />
      </div>

      {/* 主区域：左活跃作业 + 右系统健康 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 360px', gap: 16, alignItems: 'start' }}>
        {/* 活跃作业列表 */}
        <OceanPanel variant="strong" padding={0}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '12px 16px',
              borderBottom: '1px solid var(--ocean-border-subtle)',
            }}
          >
            <Typography.Title level={5} style={{ margin: 0, fontSize: 15, fontWeight: 600, color: 'var(--ocean-text-primary)' }}>
              最近作业
            </Typography.Title>
          </div>
          <div style={{ padding: 0 }}>
            {jobsError ? (
              <FeedbackState
                state="error"
                description="作业列表加载失败"
                action={
                  <Button type="primary" size="small" onClick={() => void navigate({ to: '/jobs' })}>
                    前往作业中心
                  </Button>
                }
              />
            ) : recentJobs.length === 0 && !jobsLoading ? (
              <FeedbackState state="empty" title="暂无作业记录" />
            ) : (
              <Table<JobListItem>
                columns={jobColumns}
                dataSource={recentJobs}
                rowKey="id"
                loading={jobsLoading}
                size="small"
                pagination={false}
                scroll={{ x: 560 }}
                onRow={(record) => ({
                  onClick: () => void navigate({ to: '/jobs/$jobId', params: { jobId: record.id } }),
                  style: { cursor: 'pointer' },
                })}
              />
            )}
          </div>
        </OceanPanel>

        {/* 系统健康摘要 */}
        <OceanPanel variant="default" padding={16}>
          <Typography.Title level={5} style={{ margin: 0, marginBottom: 12, fontSize: 15, fontWeight: 600, color: 'var(--ocean-text-primary)' }}>
            系统健康
          </Typography.Title>
          {healthLoading ? (
            <FeedbackState state="loading" title="检查中…" style={{ padding: 24 }} />
          ) : healthError || !health ? (
            <FeedbackState
              state="error"
              title="健康检查不可用"
              description="可能后端未就绪或网络异常"
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <StatusMark
                semantic={HEALTH_SEMANTIC[health.status] ?? 'neutral'}
                label={health.status === 'healthy' || health.status === 'ok' ? '正常' : health.status === 'degraded' ? '降级' : '异常'}
                shape="dot"
              />
              {health.migration_version && (
                <Text style={{ fontSize: 12, color: 'var(--ocean-text-secondary)' }}>
                  迁移版本：<span style={{ fontFamily: 'var(--ocean-font-mono)' }}>{health.migration_version}</span>
                </Text>
              )}
              <Text style={{ fontSize: 12, color: 'var(--ocean-text-secondary)' }}>
                待处理事件：<span className="ocean-tabular-nums">{health.outbox_backlog}</span>
              </Text>
              {health.checks.length > 0 && (
                <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {health.checks.map((c) => (
                    <div key={c.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                      <Text style={{ fontSize: 12, color: 'var(--ocean-text-secondary)' }}>{c.name}</Text>
                      <StatusMark
                        semantic={c.status === 'healthy' || c.status === 'ok' ? 'success' : c.status === 'degraded' ? 'warning' : 'danger'}
                        label={c.status === 'healthy' || c.status === 'ok' ? '正常' : c.status}
                        shape="dot"
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </OceanPanel>
      </div>

      {/* 快捷入口 */}
      <div style={{ marginTop: 24 }}>
        <Typography.Title level={5} style={{ marginBottom: 12, fontSize: 15, fontWeight: 600, color: 'var(--ocean-text-primary)' }}>
          快捷入口
        </Typography.Title>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
            gap: 12,
          }}
        >
          {QUICK_ENTRIES.map((entry) => (
            <OceanPanel
              key={entry.label}
              variant="default"
              padding={16}
              style={{ cursor: 'pointer', transition: 'all 180ms var(--ocean-motion-easing)' }}
            >
              <div
                onClick={() => void navigate({ to: entry.to, search: entry.search })}
                style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
              >
                <Text style={{ fontSize: 15, fontWeight: 600, color: 'var(--ocean-text-primary)' }}>
                  {entry.label}
                </Text>
                <Text style={{ fontSize: 12, color: 'var(--ocean-text-secondary)' }}>
                  {entry.desc}
                </Text>
              </div>
            </OceanPanel>
          ))}
        </div>
      </div>
    </div>
  );
}
