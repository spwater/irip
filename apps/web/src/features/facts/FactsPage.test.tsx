import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import {
  apiListFacts,
  apiSearchFacts,
  apiSearchFactsByData,
  apiDeleteFact,
  apiDeleteFactsByTask,
} from '@/api/facts-provenance';
import { apiListDepartments } from '@/api/departments';
import { FactsPage } from './FactsPage';
import type { FactSummary } from '@/api/types';

vi.mock('@/api/facts-provenance', () => ({
  apiListFacts: vi.fn(),
  apiSearchFacts: vi.fn(),
  apiSearchFactsByData: vi.fn(),
  apiDeleteFact: vi.fn(),
  apiDeleteFactsByTask: vi.fn(),
}));

vi.mock('@/api/departments', () => ({
  apiListDepartments: vi.fn(),
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

const mockFacts: FactSummary[] = [
  { fact_id: 'f1', fact_type: 'experiment_run', subject_id: '样品A', status: 'published', task_code: 'T001', task_name: '烧结实验', project_name: '烧结项目', department_name: '实验室A', operator: '张三', run_operator: '李四', equipment_name: '光谱仪', data_summary: '测试摘要', created_at: '2025-01-01T00:00:00Z' },
  { fact_id: 'f2', fact_type: 'experiment_run', subject_id: '样品B', status: 'published', task_code: 'T001', task_name: '烧结实验', project_name: '烧结项目', department_name: '实验室A', operator: '张三', run_operator: '李四', equipment_name: '光谱仪', data_summary: null, created_at: '2025-01-02T00:00:00Z' },
];

const mockFactListResult = {
  items: mockFacts,
  next_cursor: null,
  has_more: false,
  group_counts: { T001: 2 },
};

function renderPage(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <FactsPage />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe('FactsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListFacts).mockResolvedValue(mockFactListResult);
    vi.mocked(apiSearchFacts).mockResolvedValue(mockFactListResult);
    vi.mocked(apiSearchFactsByData).mockResolvedValue(mockFactListResult);
    vi.mocked(apiDeleteFact).mockResolvedValue(undefined);
    vi.mocked(apiDeleteFactsByTask).mockResolvedValue(undefined);
    vi.mocked(apiListDepartments).mockResolvedValue({
      items: [{ id: 'd1', code: 'lab-a', display_name: '实验室A', description: null, status: 'active', sort_order: 0, member_count: 5, parent_id: null, children_count: 0, equipment_count: 1 }],
      next_cursor: null,
      has_more: false,
    });
  });

  it('renders search input and department filter', async () => {
    renderPage();
    expect(screen.getByPlaceholderText('搜索事实...')).toBeInTheDocument();
    // The department filter Select shows '全部' option
    expect(await screen.findByText('全部')).toBeInTheDocument();
  });

  it('renders fact data grouped by task', async () => {
    renderPage();
    // The task group header should appear
    expect(await screen.findByText('烧结实验')).toBeInTheDocument();
    // The task code should also appear
    expect(screen.getByText('T001')).toBeInTheDocument();
  });

  it('shows fact count in group row', async () => {
    renderPage();
    await screen.findByText('烧结实验');
    expect(screen.getByText('2 个样品')).toBeInTheDocument();
  });

  it('renders fact subject IDs after expanding task group', async () => {
    renderPage();
    // Wait for task group to appear
    await screen.findByText('烧结实验');
    // The tree table should have expandable rows; expand the task group
    const expandBtn = document.querySelector('.ant-table-row-expand-icon-collapsed') as HTMLElement;
    if (expandBtn) {
      await userEvent.click(expandBtn);
    }
    // After expanding, child rows with subject IDs should be visible
    expect(await screen.findByText('样品A', undefined, { timeout: 3000 })).toBeInTheDocument();
  });

  it('accepts text input in search field', async () => {
    renderPage();
    await screen.findByText('烧结实验');
    const searchInput = screen.getByPlaceholderText('搜索事实...');
    await userEvent.type(searchInput, '高温试验');
    expect(screen.getByDisplayValue('高温试验')).toBeInTheDocument();
  });
});
