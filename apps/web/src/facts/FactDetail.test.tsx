import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock API client
vi.mock('@/api/client', () => ({
  apiGetFact: vi.fn(),
  apiGetFactData: vi.fn(),
  apiGetArtifactDownloadUrl: vi.fn(),
  apiListFactRevisions: vi.fn(),
  apiGetFactObservations: vi.fn(),
}));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => () => {},
  useParams: () => ({ factId: 'fact-001' }),
}));

import {
  apiGetFact,
  apiGetFactData,
  apiListFactRevisions,
  apiGetFactObservations,
} from '@/api/client';
import { FactDetail } from '@/facts/FactDetail';
import type {
  FactDetail as FactDetailType,
  FactData,
  FactRevision,
  ObservationsResponse,
} from '@/api/client';

const mockFact: FactDetailType = {
  fact_id: 'fact-001',
  revision: 1,
  revision_id: 'rev-001',
  fact_type: 'measurement',
  subject_id: 'obj-001',
  status: 'active',
};

const mockFactData: FactData = {
  metadata: { unit: 'MPa' },
  data: [{ sample: 1, value: 42.5, unit: 'MPa' }],
};

const mockRevisions: { items: FactRevision[]; next_cursor: string | null } = {
  items: [
    { fact_id: 'fact-001', revision: 1, revision_id: 'rev-001', fact_type: 'measurement', subject_id: 'obj-001', status: 'active' },
  ],
  next_cursor: null,
};

const mockObservations: ObservationsResponse = {
  raw: [
    { id: 'obs-001', fact_revision_id: 'rev-001', source_path: 'Sheet1.A1', source_value: '42.5', source_unit: 'MPa', source_name: 'pressure', artifact_id: null },
  ],
  normalized: [
    { id: 'nobs-001', fact_revision_id: 'rev-001', variable_version_id: 'var-001', raw_observation_id: 'obs-001', value: '42.5', unit: 'MPa' },
  ],
};

function renderWithProviders(ui: React.ReactElement): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('FactDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGetFact).mockResolvedValue(mockFact);
    vi.mocked(apiGetFactData).mockResolvedValue(mockFactData);
    vi.mocked(apiListFactRevisions).mockResolvedValue(mockRevisions);
    vi.mocked(apiGetFactObservations).mockResolvedValue(mockObservations);
  });

  it('renders level-1 heading with 事实', async () => {
    renderWithProviders(<FactDetail />);
    expect(await screen.findByRole('heading', { level: 1, name: /事实/ })).toBeVisible();
  });

  it('renders fact ID with ocean-tech class', async () => {
    renderWithProviders(<FactDetail />);
    const elems = await screen.findAllByText('fact-001');
    expect(elems.length).toBeGreaterThan(0);
    expect(elems[0]).toHaveClass('ocean-tech');
  });

  it('renders unit MPa visible on the page', async () => {
    renderWithProviders(<FactDetail />);
    // Wait for fact to load
    await screen.findByRole('heading', { level: 1, name: /事实/ });
    // MPa appears in observations and data
    expect(screen.getAllByText('MPa').length).toBeGreaterThan(0);
  });

  it('renders all four named regions', async () => {
    renderWithProviders(<FactDetail />);
    await screen.findByRole('heading', { level: 1, name: /事实/ });

    for (const name of ['事实元数据', '版本历史', '观测数据', '原始数据']) {
      expect(screen.getByRole('region', { name })).toBeVisible();
    }
  });

  it('shows retry action on API failure without empty-data message', async () => {
    vi.mocked(apiGetFact).mockRejectedValue(new Error('Network error'));
    renderWithProviders(<FactDetail />);

    expect(await screen.findByText('事实详情获取失败')).toBeVisible();
    expect(screen.getByText('重试')).toBeVisible();
    // Should NOT show empty-data message
    expect(screen.queryByText('未找到数据')).not.toBeInTheDocument();
  });
});
