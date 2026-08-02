import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Table, Typography, DatePicker, Select, Space } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useNavigate } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { apiListFacts } from '@/api/facts';
import { apiListFlows } from '@/api/flows';
import { apiListJobs } from '@/api/jobs';
import type { JobListItem } from '@/api/governance';
import { apiGetSystemHealth, type SystemHealth } from '@/api/system';
import { apiListEquipment } from '@/api/equipment-flows';
import { MetricStrip, OceanPanel, FeedbackState, StatusMark, DataHero, EcgLine } from '@/shared/ui';
import type { StatusSemantic } from '@/theme/tokens';
import { usePageHeaderRegistration } from '@/app/PageHeaderContext';
import { CHART_COLOR_SEQUENCE } from '@/theme/chartTheme';
import { tokens } from '@/theme/tokens';

const { Text } = Typography;
const { RangePicker } = DatePicker;

/** 作业状态 -> 语义映射 */
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

/** 作业状态 -> 中文标签 */
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

/** 系统健康状态 -> 语义映射 */
const HEALTH_SEMANTIC: Record<string, StatusSemantic> = {
  healthy: 'success',
  ok: 'success',
  degraded: 'warning',
  unhealthy: 'danger',
  error: 'danger',
};

// ---- 环形图组件 ----

type DonutDatum = { name: string; value: number };

interface DonutChartProps {
  title: string;
  data: DonutDatum[];
  loading?: boolean;
  height?: number;
}

/** 带圆润凸台效果的环形图 */
function DonutChart({ title, data, loading, height = 220 }: DonutChartProps): JSX.Element {
  const chartRef = useRef<HTMLDivElement>(null);
  const [echart, setEchart] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    import('echarts').then((echarts) => {
      if (!cancelled && chartRef.current) {
        const chart = echarts.init(chartRef.current, undefined, { width: chartRef.current.offsetWidth, height });
        setEchart(chart);
      }
    });
    return () => { cancelled = true; };
  }, [height]);

  useEffect(() => {
    if (!echart) return;
    import('echarts').then(() => {
      if (!chartRef.current || !echart) return;
      const chart = echart as { setOption: (opt: unknown) => void; resize: () => void };
      const hasData = data.some((d) => d.value > 0);
      chart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
          trigger: 'item',
          backgroundColor: tokens.ocean.surface.strong,
          borderColor: tokens.ocean.border.strong,
          borderWidth: 1,
          textStyle: { color: tokens.ocean.text.primary, fontSize: 12 },
          formatter: '{b}: {c} ({d}%)',
        },
        legend: {
          show: data.length <= 6,
          bottom: 0,
          left: 'center',
          itemWidth: 8,
          itemHeight: 8,
          itemGap: 10,
          textStyle: { color: tokens.ocean.text.secondary, fontSize: 11 },
        },
        series: [{
          name: title,
          type: 'pie',
          radius: ['42%', '68%'],
          center: ['50%', '42%'],
          avoidLabelOverlap: true,
          itemStyle: {
            borderRadius: 6,
            borderColor: tokens.ocean.surface.strong,
            borderWidth: 2,
          },
          label: {
            show: true,
            position: 'center',
            formatter: hasData
              ? `{a|${data.reduce((s, d) => s + d.value, 0)}}\n{b|总数}`
              : '{b|暂无数据}',
            rich: {
              a: { fontSize: 28, fontWeight: 700, color: tokens.ocean.text.primary, lineHeight: 34 },
              b: { fontSize: 12, color: tokens.ocean.text.secondary, lineHeight: 16 },
            },
          },
          emphasis: {
            itemStyle: {
              borderRadius: 10,
              shadowBlur: 12,
              shadowColor: 'rgba(14, 91, 132, 0.28)',
            },
            label: { show: true },
          },
          data: hasData ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: 'rgba(72, 107, 126, 0.12)' } }],
          color: CHART_COLOR_SEQUENCE,
        }],
      });
      chart.resize();
    });
  }, [data, echart, title]);

  useEffect(() => {
    if (!echart) return;
    const handleResize = () => { (echart as { resize: () => void }).resize(); };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [echart]);

  return (
    <OceanPanel variant="default" padding={0}>
      <div style={{ padding: '10px 14px 2px', borderBottom: '1px solid var(--ocean-border-subtle)' }}>
        <Typography.Text style={{ fontSize: 13, fontWeight: 600, color: 'var(--ocean-text-primary)' }}>
          {title}
        </Typography.Text>
      </div>
      <div ref={chartRef} style={{ width: '100%', height, opacity: loading ? 0.4 : 1, transition: 'opacity 200ms' }} />
    </OceanPanel>
  );
}

// ---- 趋势图组件 ----

interface TrendChartProps {
  data: { date: string; count: number }[];
  loading?: boolean;
  height?: number;
  dateRange: [Dayjs, Dayjs];
  equipFilter: string | undefined;
  equipOptions: { label: string; value: string }[];
  onDateChange: (dates: [Dayjs, Dayjs] | null) => void;
  onEquipChange: (val: string | undefined) => void;
}

/** 数据入库趋势图（柱状图 + 筛选器） */
function TrendChart({
  data, loading, height = 280, dateRange, equipFilter,
  equipOptions, onDateChange, onEquipChange,
}: TrendChartProps): JSX.Element {
  const chartRef = useRef<HTMLDivElement>(null);
  const [echart, setEchart] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    import('echarts').then((echarts) => {
      if (!cancelled && chartRef.current) {
        const chart = echarts.init(chartRef.current, undefined, { width: chartRef.current.offsetWidth, height });
        setEchart(chart);
      }
    });
    return () => { cancelled = true; };
  }, [height]);

  useEffect(() => {
    if (!echart) return;
    import('echarts').then(() => {
      if (!chartRef.current || !echart) return;
      const chart = echart as { setOption: (opt: unknown) => void; resize: () => void };
      chart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
          trigger: 'axis',
          backgroundColor: tokens.ocean.surface.strong,
          borderColor: tokens.ocean.border.strong,
          borderWidth: 1,
          textStyle: { color: tokens.ocean.text.primary, fontSize: 12 },
          formatter: (params: { name: string; value: number }[]) => {
            const p = params[0];
            return p ? `${p.name}<br/>入库 <b>${p.value}</b> 条` : '';
          },
        },
        grid: { left: 40, right: 16, top: 12, bottom: 40 },
        xAxis: {
          type: 'category',
          data: data.map((d) => d.date),
          axisLine: { lineStyle: { color: 'rgba(24, 102, 133, 0.20)' } },
          axisTick: { show: false },
          axisLabel: { color: tokens.ocean.text.secondary, fontSize: 11, rotate: data.length > 20 ? 35 : 0 },
        },
        yAxis: {
          type: 'value',
          minInterval: 1,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: tokens.ocean.text.secondary, fontSize: 11 },
          splitLine: { lineStyle: { color: 'rgba(24, 102, 133, 0.12)' } },
        },
        series: [{
          type: 'bar',
          data: data.map((d) => d.count),
          barMaxWidth: 28,
          itemStyle: {
            borderRadius: [4, 4, 0, 0],
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: '#0E5B84' },
                { offset: 1, color: 'rgba(14, 91, 132, 0.32)' },
              ],
            },
          },
          emphasis: {
            itemStyle: {
              color: {
                type: 'linear',
                x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: '#17B8CE' },
                  { offset: 1, color: 'rgba(23, 184, 206, 0.45)' },
                ],
              },
            },
          },
        }],
      });
      chart.resize();
    });
  }, [data, echart]);

  useEffect(() => {
    if (!echart) return;
    const handleResize = () => { (echart as { resize: () => void }).resize(); };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [echart]);

  return (
    <OceanPanel variant="default" padding={0}>
      {/* 标题 + 筛选器 */}
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--ocean-border-subtle)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <Typography.Text style={{ fontSize: 13, fontWeight: 600, color: 'var(--ocean-text-primary)' }}>
            数据入库趋势
          </Typography.Text>
          <Space size={8} wrap>
            <RangePicker
              size="small"
              value={dateRange}
              onChange={(dates) => onDateChange(dates as [Dayjs, Dayjs] | null)}
              style={{ fontSize: 12 }}
            />
            <Select
              size="small"
              placeholder="设备"
              allowClear
              value={equipFilter}
              onChange={onEquipChange}
              options={equipOptions}
              style={{ width: 130, fontSize: 12 }}
            />
          </Space>
        </div>
      </div>
      <div ref={chartRef} style={{ width: '100%', height, opacity: loading ? 0.4 : 1, transition: 'opacity 200ms' }} />
    </OceanPanel>
  );
}

/**
 * 研发看板页面 -- 真实数据总览
 */
export function WorkbenchPage(): JSX.Element {
  const navigate = useNavigate();

  usePageHeaderRegistration({
    index: 'MODULE 01 / RESEARCH WORKBENCH',
    title: '研发看板',
  }, []);

  // ---- 查询 1：事实列表（30秒轮询） ----
  const { data: factsData } = useQuery({
    queryKey: ['workbench', 'facts'],
    queryFn: () => apiListFacts({ page_size: 100 }),
    refetchInterval: 30000,
  });
  const factsItems = factsData?.items ?? [];
  const factsCount = factsItems.length;

  // ---- 查询 2：流程列表 ----
  const { data: flowsData } = useQuery({
    queryKey: ['workbench', 'flows'],
    queryFn: () => apiListFlows(),
  });
  const flowsItems = flowsData?.items ?? [];
  const flowsCount = flowsItems.length;
  const activeFlows = flowsItems.filter((f) => f.status === 'published').length;

  // ---- 查询 3：作业列表 ----
  const { data: jobsData, isLoading: jobsLoading, error: jobsError } = useQuery({
    queryKey: ['workbench', 'jobs'],
    queryFn: () => apiListJobs({ limit: 50 }),
  });
  const jobsItems = jobsData?.items ?? [];
  const recentJobs = jobsItems.slice(0, 8);
  const activeJobCount = jobsItems.filter(
    (j) => ['accepted', 'queued', 'running', 'retry_wait'].includes(j.status),
  ).length;

  // ---- 查询 4：系统健康 ----
  const { data: healthData, isLoading: healthLoading, error: healthError } = useQuery({
    queryKey: ['workbench', 'health'],
    queryFn: () => apiGetSystemHealth(),
    retry: false,
  });
  const health: SystemHealth | undefined = healthData;

  // ---- 查询 5：设备列表 ----
  const { data: equipData } = useQuery({
    queryKey: ['workbench', 'equipment'],
    queryFn: () => apiListEquipment({ limit: 100 }),
  });
  const equipment = equipData?.items ?? [];

  // ---- 趋势图筛选状态 ----
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(29, 'day'),
    dayjs(),
  ]);
  const [equipFilter, setEquipFilter] = useState<string | undefined>('');

  // 设备下拉选项（含"全部"选项）
  const equipOptions = useMemo(
    () => [
      { label: '全部设备', value: '' },
      ...equipment.map((e) => ({ label: e.display_name, value: e.display_name })),
    ],
    [equipment],
  );

  // ---- 聚合：实验室数据占比 ----
  const labDonutData: DonutDatum[] = useMemo(() => {
    const map = new Map<string, number>();
    for (const f of factsItems) {
      const name = f.department_name || '未分类';
      map.set(name, (map.get(name) ?? 0) + 1);
    }
    return Array.from(map.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);
  }, [factsItems]);

  // ---- 聚合：设备数据占比 ----
  const equipDonutData: DonutDatum[] = useMemo(() => {
    const map = new Map<string, number>();
    for (const f of factsItems) {
      const name = f.equipment_name || '未分类';
      map.set(name, (map.get(name) ?? 0) + 1);
    }
    return Array.from(map.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);
  }, [factsItems]);

  // ---- 聚合：入库趋势（按天统计，支持实验室和设备筛选） ----
  const trendData = useMemo(() => {
    const start = dateRange[0].startOf('day');
    const end = dateRange[1].startOf('day');
    const days: string[] = [];
    let cursor = start;
    while (cursor.isBefore(end) || cursor.isSame(end, 'day')) {
      days.push(cursor.format('MM-DD'));
      cursor = cursor.add(1, 'day');
    }

    // 按天计数
    const dayMap = new Map<string, number>(days.map((d) => [d, 0]));

    // 从 facts 数据中按 created_at 提取日期
    for (const f of factsItems) {
      if (!f.created_at) continue;
      const factDate = dayjs(f.created_at);
      const dateStr = factDate.format('MM-DD');
      // 应用筛选
      if (equipFilter && (f.equipment_name ?? '') !== equipFilter) continue;
      if (dayMap.has(dateStr)) {
        dayMap.set(dateStr, (dayMap.get(dateStr) ?? 0) + 1);
      }
    }

    return days.map((d) => ({ date: d, count: dayMap.get(d) ?? 0 }));
  }, [dateRange, factsItems, equipFilter]);

  // 最近作业表格列
  const jobColumns: ColumnsType<JobListItem> = useMemo(
    () => [
      {
        title: '任务名称',
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
        width: 140,
        ellipsis: true,
        render: (v: string) => v || <Text type="secondary">-</Text>,
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

  const metrics = [
    { label: '事实记录（最近返回）', value: factsCount, unit: '条' },
    { label: '流程（已发布）', value: activeFlows, unit: `/${flowsCount}` },
    { label: '活跃作业', value: activeJobCount, unit: '个' },
  ];

  return (
    <div className="ocean-page-enter">
      {/* 数据主视觉：深潮 Hero + 摘要指标条（非对称构图） */}
      <div
        style={{
          display: 'flex',
          alignItems: 'stretch',
          gap: 16,
          marginBottom: 24,
          flexWrap: 'wrap',
        }}
      >
        <DataHero
          deep
          value={factsCount}
          unit="条"
          label="事实记录 · 当前返回"
          style={{ flex: '0 0 auto' }}
        />
        <div style={{ flex: 1, minWidth: 420, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <MetricStrip metrics={metrics} />
        </div>
      </div>

      {/* 趋势图（宽） + 两个饼图（窄） */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: 12, marginBottom: 16, alignItems: 'stretch' }}>
        <TrendChart
          data={trendData}
          loading={!factsData}
          dateRange={dateRange}
          equipFilter={equipFilter}
          equipOptions={equipOptions}
          onDateChange={(dates) => dates && setDateRange(dates)}
          onEquipChange={setEquipFilter}
        />
        <DonutChart title="实验室数据占比" data={labDonutData} loading={!factsData} height={280} />
        <DonutChart title="设备数据占比" data={equipDonutData} loading={!factsData} height={280} />
      </div>

      {/* 主区域：左活跃作业 + 右系统健康（两栏等高） */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 360px', gap: 16, alignItems: 'stretch' }}>
        <OceanPanel variant="strong" padding={0} style={{ height: '100%' }}>
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
                scroll={{ x: 460 }}
                onRow={(record) => ({
                  onClick: () => void navigate({ to: '/jobs/$jobId', params: { jobId: record.id } }),
                  style: { cursor: 'pointer' },
                })}
              />
            )}
          </div>
        </OceanPanel>

        <OceanPanel variant="default" padding={16} style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
          {/* 标题行：系统健康 + 状态标记 */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
            <Typography.Title level={5} style={{ margin: 0, fontSize: 15, fontWeight: 600, color: 'var(--ocean-text-primary)' }}>
              系统健康
            </Typography.Title>
            {!healthLoading && !healthError && health && (
              <StatusMark
                semantic={HEALTH_SEMANTIC[health.status] ?? 'neutral'}
                label={health.status === 'healthy' || health.status === 'ok' ? '正常' : health.status === 'degraded' ? '降级' : '异常'}
                shape="dot"
              />
            )}
          </div>
          {healthLoading ? (
            <FeedbackState state="loading" title="检查中…" style={{ padding: 24 }} />
          ) : healthError || !health ? (
            <FeedbackState
              state="error"
              title="健康检查不可用"
              description="可能后端未就绪或网络异常"
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1, justifyContent: 'space-between', gap: 8 }}>
              {/* 心电图主视觉：大脉搏图，颜色随健康状态 */}
              <EcgLine
                status={HEALTH_SEMANTIC[health.status] ?? 'neutral'}
                width="100%"
                height={72}
                stretch
              />

              {/* 版本与事件 */}
              <div style={{ display: 'flex', gap: 20 }}>
                {health.migration_version && (
                  <Text style={{ fontSize: 12, color: 'var(--ocean-text-secondary)' }}>
                    迁移版本 <span style={{ fontFamily: 'var(--ocean-font-mono)', color: 'var(--ocean-text-primary)' }}>{health.migration_version}</span>
                  </Text>
                )}
                <Text style={{ fontSize: 12, color: 'var(--ocean-text-secondary)' }}>
                  待处理事件 <span className="ocean-tabular-nums" style={{ color: 'var(--ocean-text-primary)' }}>{health.outbox_backlog}</span>
                </Text>
              </div>

              {/* 分项检查：撑满剩余空间 */}
              {health.checks.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1, justifyContent: 'space-evenly', borderTop: '1px solid var(--ocean-border-subtle)', paddingTop: 8 }}>
                  {health.checks.map((c) => (
                    <div key={c.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                      <Text style={{ fontSize: 12, color: 'var(--ocean-text-secondary)', fontFamily: 'var(--ocean-font-mono)' }}>{c.name}</Text>
                      <StatusMark
                        semantic={c.status === 'healthy' || c.status === 'ok' ? 'success' : c.status === 'degraded' ? 'warning' : 'danger'}
                        label={c.status === 'healthy' || c.status === 'ok' ? '正常' : c.status}
                        shape="dot"
                      />
                    </div>
                  ))}
                </div>
              )}

              {/* 查看详情 → 治理控制台 */}
              <Button
                type="link"
                size="small"
                onClick={() => void navigate({ to: '/governance' })}
                style={{ alignSelf: 'flex-end', padding: 0, fontSize: 12, height: 'auto' }}
              >
                查看详情 →
              </Button>
            </div>
          )}
        </OceanPanel>
      </div>
    </div>
  );
}
