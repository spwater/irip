import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock API client
vi.mock('@/api/client', () => ({
  apiListJobs: vi.fn(),
  apiCancelJob: vi.fn(),
  apiRetryJob: vi.fn(),
  extractApiError: (err: unknown) => String(err),
}));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => () => {},
}));

import { apiListJobs } from '@/api/client';
import { JobsPage } from '@/jobs/JobsPage';
import { JOB_STATUS_VIEW } from '@/jobs/jobPresentation';
import type { JobListItem, JobListResponse, JobStatus } from '@/api/client';

const jobFixtures: JobListItem[] = [
  { id: 'job-001', kind: 'echo', status: 'running', stage: '处理中', progress: 50, retryable: true, created_at: '2026-07-28T10:00:00Z', attempt: 1, max_attempts: 3 },
  { id: 'job-002', kind: 'parse_excel', status: 'succeeded', stage: '完成', progress: 100, retryable: false, created_at: '2026-07-28T09:00:00Z', attempt: 1, max_attempts: 3 },
  { id: 'job-003', kind: 'ingestion', status: 'failed', stage: '错误', progress: 80, retryable: true, created_at: '2026-07-28T08:00:00Z', attempt: 2, max_attempts: 3 },
  { id: 'job-004', kind: 'derivation', status: 'cancelled', stage: '已取消', progress: 30, retryable: false, created_at: '2026-07-28T07:00:00Z', attempt: 1, max_attempts: 3 },
];

function renderWithProviders(ui: React.ReactElement): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('JobsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListJobs).mockResolvedValue({
      items: jobFixtures,
      next_cursor: null,
      has_more: false,
    } as JobListResponse);
  });

  it('shows same status label as JOB_STATUS_VIEW for each backend status', async () => {
    renderWithProviders(<JobsPage />);

    for (const job of jobFixtures) {
      const expected = JOB_STATUS_VIEW[job.status as JobStatus].label;
      // Multiple elements may share the same label, so check at least one exists
      const elements = screen.getAllByText(expected);
      expect(elements.length).toBeGreaterThan(0);
    }
  });

  it('renders named region for job directory', async () => {
    renderWithProviders(<JobsPage />);
    expect(await screen.findByRole('region', { name: '作业目录' })).toBeVisible();
  });
});
