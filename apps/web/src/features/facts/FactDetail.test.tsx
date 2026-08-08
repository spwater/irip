import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import { apiGetFact, apiGetFactData } from '@/api/facts-provenance';
import { apiGetArtifactDownloadUrl } from '@/api/models-ai';
import { FactDetail } from './FactDetail';

vi.mock('@/api/facts-provenance', () => ({
  apiGetFact: vi.fn(),
  apiGetFactData: vi.fn(),
}));

vi.mock('@/api/models-ai', () => ({
  apiGetArtifactDownloadUrl: vi.fn(),
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useParams: () => ({ factId: 'fact-001' }),
  useSearch: () => ({}),
}));

const mockFact = {
  fact_id: 'fact-001',
  fact_type: 'experiment_run',
  subject_id: '样品A',
  status: 'published',
  visibility_scope: 'tree' as const,
};

const mockFactData = {
  metadata: { 设备型号: 'XRF-100', 环境: '室温' },
  points: [
    { name: '温度', value: 105, unit: '℃' },
    { name: '压力', value: 1.5, unit: 'MPa' },
  ],
  series: [],
  task_info: {
    task_name: '烧结实验',
    run_operator: '李四',
    equipment_name: '光谱仪',
    project_name: '烧结项目',
    owner_name: '张三',
    department_name: '实验室A',
    job_id: 'job-001',
    data_interface: 'XRF',
    created_at: '2025-01-01T00:00:00Z',
    data_source_list: [],
  },
  source_file: null,
};

function renderDetail(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <FactDetail />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe('FactDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGetFact).mockResolvedValue(mockFact);
    vi.mocked(apiGetFactData).mockResolvedValue(mockFactData as never);
    vi.mocked(apiGetArtifactDownloadUrl).mockResolvedValue('http://example.com/download');
  });

  it('renders 事实详情 page title', async () => {
    renderDetail();
    expect(await screen.findByText('事实详情')).toBeInTheDocument();
  });

  it('renders task info in import data source section', async () => {
    renderDetail();
    expect(await screen.findByText('烧结实验')).toBeInTheDocument();
    expect(screen.getByText('李四')).toBeInTheDocument();
    expect(screen.getByText('光谱仪')).toBeInTheDocument();
  });

  it('renders data detail tabs: 元数据 / 单点数据', async () => {
    renderDetail();
    expect(await screen.findByText('元数据')).toBeInTheDocument();
    expect(screen.getByText(/单点数据/)).toBeInTheDocument();
  });

  it('shows point data in table view', async () => {
    renderDetail();
    expect(await screen.findByText('温度')).toBeInTheDocument();
    expect(screen.getByText('压力')).toBeInTheDocument();
    expect(screen.getByText('105')).toBeInTheDocument();
  });

  it('shows metadata entries', async () => {
    renderDetail();
    // Click on 元数据 tab
    const metadataTab = await screen.findByText('元数据');
    await userEvent.click(metadataTab);
    expect(screen.getByText('设备型号')).toBeInTheDocument();
    expect(screen.getByText('XRF-100')).toBeInTheDocument();
  });

  it('shows 公开 button for private facts', async () => {
    vi.mocked(apiGetFact).mockResolvedValueOnce({ ...mockFact, visibility_scope: 'private' });
    renderDetail();
    // Wait for the page to load and the button to appear
    expect(await screen.findByText('事实详情', {}, { timeout: 5000 })).toBeInTheDocument();
    // The "公开" button should be visible for private facts
    const publishBtn = await screen.findByRole('button', { name: /公\s*开/ }, { timeout: 5000 });
    expect(publishBtn).toBeInTheDocument();
  });

  it('shows 返回项目 button', async () => {
    renderDetail();
    expect(await screen.findByText('返回项目')).toBeInTheDocument();
  });
});
