import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  apiListModels,
  apiGetModelVersions,
  type ModelSummary,
  type ModelVersionSummary,
} from '@/api/models-ai';
import { ModelsPage } from './ModelsPage';

vi.mock('@/api/models-ai', () => ({
  apiListModels: vi.fn(),
  apiCreateModel: vi.fn(),
  apiGetModelVersions: vi.fn(),
  apiPublishModelVersion: vi.fn(),
  apiRollbackModel: vi.fn(),
  apiDeprecateModel: vi.fn(),
}));

const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}));

const draftModel: ModelSummary = {
  id: 'm-001',
  code: 'grate_cooler',
  display_name: '篦冷机降阶模型',
  status: 'draft',
  current_version_id: null,
  lock_version: 1,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const publishedModel: ModelSummary = {
  id: 'm-002',
  code: 'sinter_model',
  display_name: '烧结性能模型',
  status: 'published',
  current_version_id: 'ver-aaa-bbb-ccc',
  lock_version: 3,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-02T00:00:00Z',
};

const validatedVersion: ModelVersionSummary = {
  id: 'ver-001',
  model_id: 'm-001',
  version: 1,
  status: 'validated',
  contract_sha256: null,
  model_artifact_id: null,
  metrics: { rmse: 0.05 },
  applicability_domain: {},
  code_hash: null,
  dependency_hash: null,
  model_hash: null,
  created_at: '2024-01-01T00:00:00Z',
  published_at: null,
};

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>{ui}</AntApp>
    </QueryClientProvider>,
  );
}

describe('ModelsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListModels).mockResolvedValue({ items: [draftModel, publishedModel], next_cursor: null, has_more: false });
    vi.mocked(apiGetModelVersions).mockResolvedValue([validatedVersion]);
  });

  it('renders 新建模型 button', () => {
    renderWithClient(<ModelsPage />);
    expect(screen.getByRole('button', { name: /新建模型/ })).toBeInTheDocument();
  });

  it('renders 预测工作台 button', () => {
    renderWithClient(<ModelsPage />);
    expect(screen.getByRole('button', { name: '预测工作台' })).toBeInTheDocument();
  });

  it('navigates to predict page when 预测工作台 clicked', async () => {
    renderWithClient(<ModelsPage />);
    await userEvent.click(screen.getByRole('button', { name: '预测工作台' }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/models/predict' });
  });

  it('renders model rows after loading', async () => {
    renderWithClient(<ModelsPage />);
    expect(await screen.findByText('篦冷机降阶模型')).toBeInTheDocument();
    expect(screen.getByText('烧结性能模型')).toBeInTheDocument();
  });

  it('renders status tags with correct labels', async () => {
    renderWithClient(<ModelsPage />);
    expect(await screen.findByText('草稿')).toBeInTheDocument();
    expect(screen.getByText('已发布')).toBeInTheDocument();
  });

  it('renders action buttons 查看详情 发布 回滚 废弃', async () => {
    renderWithClient(<ModelsPage />);
    await screen.findByText('篦冷机降阶模型');
    expect(screen.getAllByText('查看详情').length).toBeGreaterThan(0);
    expect(screen.getAllByText('发布').length).toBeGreaterThan(0);
    expect(screen.getAllByText('回滚').length).toBeGreaterThan(0);
    expect(screen.getAllByText('废弃').length).toBeGreaterThan(0);
  });

  it('opens create modal when 新建模型 clicked', async () => {
    renderWithClient(<ModelsPage />);
    await userEvent.click(screen.getByRole('button', { name: /新建模型/ }));
    expect(await screen.findByText('模型编码')).toBeInTheDocument();
    expect(screen.getByText('模型名称')).toBeInTheDocument();
  });

  it('navigates to model detail when 查看详情 clicked', async () => {
    renderWithClient(<ModelsPage />);
    const detailButtons = await screen.findAllByText('查看详情');
    await userEvent.click(detailButtons[0]);
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/models/$modelId',
      params: { modelId: 'm-001' },
    });
  });

  it('shows validation error when creating model without code', async () => {
    renderWithClient(<ModelsPage />);
    await userEvent.click(screen.getByRole('button', { name: /新建模型/ }));
    await screen.findByText('模型编码');
    // Click the 创建 button inside modal
    const createBtn = screen.getByRole('button', { name: /创\s*建/ });
    await userEvent.click(createBtn);
    expect(await screen.findByText('请输入模型编码')).toBeInTheDocument();
  });

  it('renders create modal form fields when 新建模型 clicked', async () => {
    renderWithClient(<ModelsPage />);
    await userEvent.click(screen.getByRole('button', { name: /新建模型/ }));
    expect(await screen.findByText('模型编码')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('如：grate_cooler_rom')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('如：篦冷机降阶模型')).toBeInTheDocument();
  });

  it('does not show 废弃 button for deprecated model', async () => {
    const deprecatedModel: ModelSummary = { ...draftModel, status: 'deprecated' };
    vi.mocked(apiListModels).mockResolvedValueOnce({ items: [deprecatedModel], next_cursor: null, has_more: false });
    renderWithClient(<ModelsPage />);
    await screen.findByText('篦冷机降阶模型');
    expect(screen.queryByText('废弃')).not.toBeInTheDocument();
  });
});
