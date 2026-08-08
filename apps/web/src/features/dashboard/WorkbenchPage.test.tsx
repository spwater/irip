import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import { PageHeaderProvider } from '@/app/PageHeaderContext';
import { apiListFacts } from '@/api/facts';
import { apiListFlows } from '@/api/flows';
import { apiListJobs } from '@/api/jobs';
import { apiGetSystemHealth } from '@/api/governance';
import { apiListEquipment } from '@/api/equipment-flows';
import { WorkbenchPage } from './WorkbenchPage';

vi.mock('@/api/facts', () => ({
  apiListFacts: vi.fn(),
}));

vi.mock('@/api/flows', () => ({
  apiListFlows: vi.fn(),
}));

vi.mock('@/api/jobs', () => ({
  apiListJobs: vi.fn(),
}));

vi.mock('@/api/governance', () => ({
  apiGetSystemHealth: vi.fn(),
}));

vi.mock('@/api/equipment-flows', () => ({
  apiListEquipment: vi.fn(),
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

// Mock echarts to avoid heavy import in test
vi.mock('echarts', () => ({
  init: vi.fn(() => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  })),
}));

function renderPage(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <PageHeaderProvider>
        <AntApp>
          <WorkbenchPage />
        </AntApp>
      </PageHeaderProvider>
    </QueryClientProvider>,
  );
}

describe('WorkbenchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListFacts).mockResolvedValue({
      items: [
        { fact_id: 'f1', fact_type: 'experiment_run', subject_id: '样品A', status: 'published', task_code: 'T001', task_name: '烧结', project_name: '项目1', department_name: '实验室A', operator: '张三', run_operator: '李四', equipment_name: '光谱仪', data_summary: null, created_at: '2025-01-01T00:00:00Z' },
      ],
      next_cursor: null,
      has_more: false,
      group_counts: {},
    });
    vi.mocked(apiListFlows).mockResolvedValue({ items: [], next_cursor: null, has_more: false });
    vi.mocked(apiListJobs).mockResolvedValue({ items: [], next_cursor: null, has_more: false });
    vi.mocked(apiGetSystemHealth).mockResolvedValue({
      status: 'healthy',
      migration_version: '001',
      worker_heartbeat: null,
      outbox_backlog: 0,
      checks: [{ name: 'db', status: 'healthy', latency_ms: 10, message: null }],
    });
    vi.mocked(apiListEquipment).mockResolvedValue({ items: [], next_cursor: null, has_more: false });
  });

  it('renders 研发看板 title section', async () => {
    renderPage();
    expect(await screen.findByText('事实记录 · 当前返回')).toBeInTheDocument();
  });

  it('renders 最近作业 section', async () => {
    renderPage();
    expect(await screen.findByText('最近作业')).toBeInTheDocument();
  });

  it('renders 系统健康 section', async () => {
    renderPage();
    expect(await screen.findByText('系统健康')).toBeInTheDocument();
  });

  it('renders 数据入库趋势 section', async () => {
    renderPage();
    expect(await screen.findByText('数据入库趋势')).toBeInTheDocument();
  });

  it('renders 实验室数据占比 and 设备数据占比 donut charts', async () => {
    renderPage();
    expect(await screen.findByText('实验室数据占比')).toBeInTheDocument();
    expect(screen.getByText('设备数据占比')).toBeInTheDocument();
  });

  it('shows empty state for jobs when no data', async () => {
    renderPage();
    expect(await screen.findByText('暂无作业记录')).toBeInTheDocument();
  });
});
