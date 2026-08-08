import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  apiGetModel,
  apiGetModelVersions,
  type ModelSummary,
  type ModelVersionSummary,
} from '@/api/models-ai';
import { ModelDetail } from './ModelDetail';

vi.mock('@/api/models-ai', () => ({
  apiGetModel: vi.fn(),
  apiGetModelVersions: vi.fn(),
  apiPublishModelVersion: vi.fn(),
  apiRollbackModel: vi.fn(),
  apiValidateModelVersion: vi.fn(),
}));

const mockNavigate = vi.fn();
let mockParams: Record<string, unknown> = {};
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => mockParams,
}));

const model: ModelSummary = {
  id: 'm-001',
  code: 'sinter_model',
  display_name: '烧结性能模型',
  status: 'published',
  current_version_id: 'ver-001',
  lock_version: 2,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-02T00:00:00Z',
};

const version1: ModelVersionSummary = {
  id: 'ver-001',
  model_id: 'm-001',
  version: 1,
  status: 'published',
  contract_sha256: null,
  model_artifact_id: null,
  metrics: { rmse: 0.05, r2: 0.98 },
  applicability_domain: {
    temperature: { min: 100, max: 300, unit: 'C' },
  },
  code_hash: null,
  dependency_hash: null,
  model_hash: null,
  created_at: '2024-01-01T00:00:00Z',
  published_at: '2024-01-01T00:00:00Z',
};

const version2: ModelVersionSummary = {
  id: 'ver-002',
  model_id: 'm-001',
  version: 2,
  status: 'draft',
  contract_sha256: null,
  model_artifact_id: null,
  metrics: {},
  applicability_domain: {},
  code_hash: null,
  dependency_hash: null,
  model_hash: null,
  created_at: '2024-01-02T00:00:00Z',
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

describe('ModelDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockParams = { modelId: 'm-001' };
    vi.mocked(apiGetModel).mockResolvedValue(model);
    vi.mocked(apiGetModelVersions).mockResolvedValue([version1, version2]);
  });

  it('renders 返回列表 button', async () => {
    renderWithClient(<ModelDetail />);
    expect(await screen.findByText('返回列表')).toBeInTheDocument();
  });

  it('navigates back when 返回列表 clicked', async () => {
    renderWithClient(<ModelDetail />);
    await userEvent.click(await screen.findByText('返回列表'));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/models' });
  });

  it('renders model detail card with code and name', async () => {
    renderWithClient(<ModelDetail />);
    expect(await screen.findByText('模型详情')).toBeInTheDocument();
    expect(screen.getByText('sinter_model')).toBeInTheDocument();
    expect(screen.getByText('烧结性能模型')).toBeInTheDocument();
  });

  it('renders 已发布 status tag', async () => {
    renderWithClient(<ModelDetail />);
    expect((await screen.findAllByText('已发布')).length).toBeGreaterThan(0);
  });

  it('renders lock version and update time', async () => {
    renderWithClient(<ModelDetail />);
    expect(await screen.findByText('2')).toBeInTheDocument();
    expect(screen.getByText('2024-01-02T00:00:00Z')).toBeInTheDocument();
  });

  it('renders action buttons 提交验证 发布版本 回滚 前往预测', async () => {
    renderWithClient(<ModelDetail />);
    expect(await screen.findByText('提交验证')).toBeInTheDocument();
    expect(screen.getByText('发布版本')).toBeInTheDocument();
    expect(screen.getByText(/回\s*滚/)).toBeInTheDocument();
    expect(screen.getByText('前往预测')).toBeInTheDocument();
  });

  it('renders version history table with versions', async () => {
    renderWithClient(<ModelDetail />);
    expect(await screen.findByText('版本历史')).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
  });

  it('renders version status tags', async () => {
    renderWithClient(<ModelDetail />);
    expect(await screen.findByText('版本历史'));
    // v1 published, v2 draft
    expect(screen.getAllByText('已发布').length).toBeGreaterThan(0);
    expect(screen.getByText('草稿')).toBeInTheDocument();
  });

  it('renders metrics tags for version with metrics', async () => {
    renderWithClient(<ModelDetail />);
    expect(await screen.findByText('rmse: 0.05')).toBeInTheDocument();
    expect(screen.getByText('r2: 0.98')).toBeInTheDocument();
  });

  it('renders applicability domain card for current version', async () => {
    renderWithClient(<ModelDetail />);
    expect(await screen.findByText('适用域范围（当前版本）')).toBeInTheDocument();
    expect(screen.getByText('temperature')).toBeInTheDocument();
  });

  it('navigates to predict when 前往预测 clicked', async () => {
    renderWithClient(<ModelDetail />);
    await userEvent.click(await screen.findByText('前往预测'));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/models/predict',
      search: { modelId: 'm-001' },
    });
  });

  it('opens validate modal when 提交验证 clicked', async () => {
    renderWithClient(<ModelDetail />);
    await screen.findByText('模型详情');
    await userEvent.click(screen.getByText('提交验证'));
    await waitFor(() => {
      expect(screen.getByText('提交版本验证')).toBeInTheDocument();
    });
  });

  it('opens publish modal when 发布版本 clicked', async () => {
    renderWithClient(<ModelDetail />);
    await screen.findByText('模型详情');
    await userEvent.click(screen.getByText('发布版本'));
    await waitFor(() => {
      expect(screen.getByText('发布模型版本')).toBeInTheDocument();
    });
  });

  it('shows 未找到模型 when model is null', async () => {
    vi.mocked(apiGetModel).mockResolvedValueOnce(undefined as unknown as ModelSummary);
    renderWithClient(<ModelDetail />);
    expect(await screen.findByText('未找到模型')).toBeInTheDocument();
  });
});
