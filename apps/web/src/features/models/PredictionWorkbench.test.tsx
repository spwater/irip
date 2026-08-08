import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  apiListModels,
  apiGetModel,
  apiGetModelVersions,
  type ModelSummary,
  type ModelVersionSummary,
} from '@/api/models-ai';
import { PredictionWorkbench } from './PredictionWorkbench';

vi.mock('@/api/models-ai', () => ({
  apiListModels: vi.fn(),
  apiGetModel: vi.fn(),
  apiGetModelVersions: vi.fn(),
  apiPredictModel: vi.fn(),
}));

const mockNavigate = vi.fn();
let mockSearch: Record<string, unknown> = {};
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useSearch: () => mockSearch,
}));

const publishedModel: ModelSummary = {
  id: 'm-001',
  code: 'sinter_model',
  display_name: '烧结性能模型',
  status: 'published',
  current_version_id: 'ver-001',
  lock_version: 1,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const draftModel: ModelSummary = {
  id: 'm-002',
  code: 'draft_model',
  display_name: '草稿模型',
  status: 'draft',
  current_version_id: null,
  lock_version: 1,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const currentVersion: ModelVersionSummary = {
  id: 'ver-001',
  model_id: 'm-001',
  version: 1,
  status: 'published',
  contract_sha256: null,
  model_artifact_id: null,
  metrics: {},
  applicability_domain: {
    temperature: { min: 100, max: 300, unit: 'C' },
  },
  code_hash: null,
  dependency_hash: null,
  model_hash: null,
  created_at: '2024-01-01T00:00:00Z',
  published_at: '2024-01-01T00:00:00Z',
};

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>{ui}</AntApp>
    </QueryClientProvider>,
  );
}

describe('PredictionWorkbench', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSearch = {};
    vi.mocked(apiListModels).mockResolvedValue({ items: [publishedModel, draftModel], next_cursor: null, has_more: false });
    vi.mocked(apiGetModel).mockResolvedValue(publishedModel);
    vi.mocked(apiGetModelVersions).mockResolvedValue([currentVersion]);
  });

  it('renders title 预测工作台', () => {
    renderWithClient(<PredictionWorkbench />);
    expect(screen.getByText('预测工作台')).toBeInTheDocument();
  });

  it('renders 返回模型列表 button', () => {
    renderWithClient(<PredictionWorkbench />);
    expect(screen.getByText('返回模型列表')).toBeInTheDocument();
  });

  it('navigates back when 返回模型列表 clicked', async () => {
    renderWithClient(<PredictionWorkbench />);
    await userEvent.click(screen.getByText('返回模型列表'));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/models' });
  });

  it('renders model selection placeholder', () => {
    renderWithClient(<PredictionWorkbench />);
    expect(screen.getByText('选择已发布的模型')).toBeInTheDocument();
  });

  it('renders empty state when no model selected', () => {
    renderWithClient(<PredictionWorkbench />);
    expect(screen.getByText('请选择一个已发布的模型')).toBeInTheDocument();
  });

  it('renders 模型选择 card title', () => {
    renderWithClient(<PredictionWorkbench />);
    expect(screen.getByText('模型选择')).toBeInTheDocument();
  });

  it('uses initial modelId from search params', async () => {
    mockSearch = { modelId: 'm-001' };
    renderWithClient(<PredictionWorkbench />);
    await waitFor(() => {
      expect(apiGetModel).toHaveBeenCalledWith('m-001');
    });
  });

  it('renders 运行模型 button when model selected via search params', async () => {
    mockSearch = { modelId: 'm-001' };
    renderWithClient(<PredictionWorkbench />);
    await waitFor(() => {
      expect(screen.getByText('运行模型')).toBeInTheDocument();
    });
  });

  it('renders temperature input field when model selected via search params', async () => {
    mockSearch = { modelId: 'm-001' };
    renderWithClient(<PredictionWorkbench />);
    await waitFor(() => {
      expect(screen.getByText('temperature')).toBeInTheDocument();
    });
  });
});
