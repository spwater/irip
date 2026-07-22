import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import type { SourcePreview, MappingRankResponse } from '@/api/client';
import { apiPreviewIngestion, apiRankMappings } from '@/api/client';
import { IngestionWizard } from '@/ingestions/IngestionWizard';

// vi.mock 必须在 import 之前（vitest 会自动提升）
vi.mock('@/api/client', () => ({
  apiPreviewIngestion: vi.fn(),
  apiRankMappings: vi.fn(),
  apiCreateJob: vi.fn().mockResolvedValue({ job_id: 'job-001' }),
  apiGetJob: vi.fn().mockResolvedValue({
    id: 'job-001',
    kind: 'ingestions.import',
    status: 'succeeded',
    stage: '完成',
    progress: 100,
    retryable: false,
  }),
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

const mockMappings: MappingRankResponse = {
  candidates: [
    {
      variableVersionId: 'vv-001',
      variableCode: 'temperature',
      score: 0.95,
      reasons: ['名称匹配', '单位匹配'],
    },
    {
      variableVersionId: 'vv-002',
      variableCode: 'pressure',
      score: 0.88,
      reasons: ['名称匹配'],
    },
    {
      variableVersionId: 'vv-003',
      variableCode: 'material_type',
      score: 0.72,
      reasons: ['类型匹配'],
    },
  ],
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
    vi.mocked(apiRankMappings).mockResolvedValue(mockMappings);
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

  it('requires confirmation for every suggested mapping before ingestion', async () => {
    const { container } = renderWizard();

    // Step 0: Upload a file
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const file = new File(['test content'], 'test.xlsx', {
      type: 'application/vnd.ms-excel',
    });
    await userEvent.upload(fileInput, file);

    // Wait for "下一步" button to appear (Step 1: Preview rendered)
    const nextButton = await screen.findByRole('button', { name: /下\s*一\s*步/ });
    await userEvent.click(nextButton);

    // Wait for checkboxes to appear (Step 2: Mapping rendered)
    const checkboxes = await screen.findAllByRole('checkbox');
    expect(checkboxes).toHaveLength(3);

    // "确认并导入" button should be disabled initially
    const confirmButton = screen.getByRole('button', {
      name: /确\s*认\s*并\s*导\s*入/,
    });
    expect(confirmButton).toBeDisabled();

    // Confirm all 3 mappings
    for (let i = 0; i < 3; i++) {
      const cbs = screen.getAllByRole('checkbox');
      await userEvent.click(cbs[i]);
    }

    // "确认并导入" button should now be enabled
    expect(confirmButton).not.toBeDisabled();
  });
});
