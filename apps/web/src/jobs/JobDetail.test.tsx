import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock API client
vi.mock('@/api/client', () => ({
  apiGetJobDetail: vi.fn(),
  apiCancelJob: vi.fn(),
  apiRetryJob: vi.fn(),
  extractApiError: (err: unknown) => String(err),
}));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => () => {},
  useParams: () => ({ jobId: 'job-001' }),
}));

import { apiGetJobDetail } from '@/api/client';
import { JobDetail } from '@/jobs/JobDetail';
import { JOB_STATUS_VIEW } from '@/jobs/jobPresentation';
import type { JobDetail as JobDetailType } from '@/api/client';

const mockJob: JobDetailType = {
  id: 'job-001',
  kind: 'parse_excel',
  status: 'failed',
  stage: '解析失败',
  progress: 80,
  retryable: true,
  attempt: 2,
  max_attempts: 3,
  created_at: '2026-07-28T10:00:00Z',
  updated_at: '2026-07-28T10:05:00Z',
  created_by: 'u-admin-001',
  last_error: { code: 'PARSE_ERROR', message: 'Invalid Excel format' },
  result: null,
  payload: { file_id: 'file-001', sheet: 'Sheet1' },
};

function renderWithProviders(ui: React.ReactElement): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('JobDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGetJobDetail).mockResolvedValue(mockJob);
  });

  it('renders named regions for job detail sections', async () => {
    renderWithProviders(<JobDetail />);

    for (const name of ['作业基本信息', '输入载荷', '错误日志']) {
      expect(await screen.findByRole('region', { name })).toBeVisible();
    }
  });

  it('renders job ID with ocean-tech class', async () => {
    renderWithProviders(<JobDetail />);
    const jobIdelem = await screen.findByText('job-001');
    expect(jobIdelem).toHaveClass('ocean-tech');
  });

  it('renders retry button for failed retryable job', async () => {
    renderWithProviders(<JobDetail />);
    expect(await screen.findByRole('button', { name: '重试作业' })).toBeVisible();
  });

  it('shows same status label as JOB_STATUS_VIEW', async () => {
    renderWithProviders(<JobDetail />);
    const expectedLabel = JOB_STATUS_VIEW['failed'].label;
    expect(await screen.findAllByText(expectedLabel)).then((elements) => {
      expect(elements.length).toBeGreaterThan(0);
    });
  });
});
