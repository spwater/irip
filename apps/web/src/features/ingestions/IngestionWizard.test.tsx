import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import type { SourcePreview } from '@/api/types';
import { apiPreviewIngestion } from '@/api/standards-objects';
import { IngestionWizard } from '@/features/ingestions/IngestionWizard';

// M-08: mock useNavigate（IngestionWizard 在 401 时跳转登录页）
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

// vi.mock 必须在 import 之前（vitest 会自动提升）
vi.mock('@/api/client', () => ({
  apiCreateJob: vi.fn().mockResolvedValue({ job_id: 'job-001' }),
  apiGetJob: vi.fn().mockResolvedValue({
    id: 'job-001',
    kind: 'ingestions.import',
    status: 'succeeded',
    stage: '完成',
    progress: 100,
    retryable: false,
  }),
}));

vi.mock('@/api/standards-objects', () => ({
  apiPreviewIngestion: vi.fn(),
}));

vi.mock('@/api/types', () => ({
  extractApiError: (err: unknown) =>
    err instanceof Error ? err.message : '操作失败',
}));

const mockPreview: SourcePreview = {
  columns: [
    { name: '温度', inferred_type: 'number', sample_values: [100, 105, 110] },
    { name: '压力', inferred_type: 'number', sample_values: [1.5, 1.6, 1.7] },
    { name: '材料', inferred_type: 'string', sample_values: ['铝合金', '钛合金', '钢'] },
  ],
  rows: [
    { 温度: 100, 压力: 1.5, 材料: '铝合金' },
    { 温度: 105, 压力: 1.6, 材料: '钛合金' },
    { 温度: 110, 压力: 1.7, 材料: '钢' },
  ],
  total_rows: 3,
};

function renderWizard(): { container: HTMLElement } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <IngestionWizard />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe('IngestionWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiPreviewIngestion).mockResolvedValue(mockPreview);
  });

  it('shows preview data from the source', async () => {
    const { container } = renderWizard();

    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    expect(fileInput).toBeTruthy();

    const file = new File(['test content'], 'test.xlsx', {
      type: 'application/vnd.ms-excel',
    });
    await userEvent.upload(fileInput, file);

    // Wait for preview table to render source columns
    expect(await screen.findByRole('columnheader', { name: '温度' })).toBeVisible();
    expect(screen.getByRole('columnheader', { name: '压力' })).toBeVisible();
    expect(screen.getByRole('columnheader', { name: '材料' })).toBeVisible();
  });

  it('renders submit button after preview', async () => {
    const { container } = renderWizard();

    // Step 0: Upload a file
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const file = new File(['test content'], 'test.xlsx', {
      type: 'application/vnd.ms-excel',
    });
    await userEvent.upload(fileInput, file);

    // Wait for "提交" button to appear (Step 1: Preview rendered)
    const submitButton = await screen.findByRole('button', { name: /提\s*交/ });
    expect(submitButton).toBeInTheDocument();
  });
});
