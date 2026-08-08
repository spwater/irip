import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import {
  apiListEvidenceSets,
  apiListRecipes,
  apiListDerivationRuns,
  apiGetProvenanceGraph,
} from '@/api/facts-provenance';
import { ProvenancePage } from './ProvenancePage';
import type { EvidenceSet, Recipe, DerivationRun, ProvenanceGraph } from '@/api/types';

vi.mock('@/api/facts-provenance', () => ({
  apiListEvidenceSets: vi.fn(),
  apiListRecipes: vi.fn(),
  apiListDerivationRuns: vi.fn(),
  apiGetProvenanceGraph: vi.fn(),
  apiCreateEvidenceSet: vi.fn(),
  apiCreateRecipe: vi.fn(),
  apiFreezeEvidenceSet: vi.fn(),
  apiPublishRecipe: vi.fn(),
  apiCreateDerivationRun: vi.fn(),
  apiReplayDerivation: vi.fn(),
}));

vi.mock('@/api/types', () => ({
  extractApiError: (err: unknown) => (err instanceof Error ? err.message : '操作失败'),
}));

const mockEvidenceSets: EvidenceSet[] = [
  { set_id: 'es-1', name: '材料性能证据集', status: 'draft', version: 1, version_id: null, member_count: 5 },
];
const mockRecipes: Recipe[] = [
  { recipe_id: 'rc-1', code: 'yield_recipe', display_name: '屈服强度配方', status: 'draft', version: 1 },
];
const mockRuns: DerivationRun[] = [
  { id: 'run-001', status: 'succeeded', output_digest: 'abc123def456', outputs: [] },
];
const mockGraph: ProvenanceGraph = {
  nodes: [
    { id: 'n1', node_type: 'fact_revision', label: '事实版本1', version: 'v1', status: 'published' },
    { id: 'n2', node_type: 'parameter_version', label: '参数版本1', version: 'v1', status: 'published' },
  ],
  edges: [
    { source_id: 'n1', source_type: 'fact_revision', target_id: 'n2', target_type: 'parameter_version', edge_type: 'derived_from' },
  ],
};

function renderPage(props?: { initialRunId?: string }): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <ProvenancePage initialRunId={props?.initialRunId} />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe('ProvenancePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListEvidenceSets).mockResolvedValue({ items: mockEvidenceSets, next_cursor: null, has_more: false });
    vi.mocked(apiListRecipes).mockResolvedValue({ items: mockRecipes, next_cursor: null, has_more: false });
    vi.mocked(apiListDerivationRuns).mockResolvedValue({ items: mockRuns, next_cursor: null, has_more: false });
    vi.mocked(apiGetProvenanceGraph).mockResolvedValue(mockGraph);
  });

  it('renders four tabs: 证据集 / 配方 / 推导运行 / 溯源图谱', () => {
    renderPage();
    expect(screen.getByText('证据集')).toBeInTheDocument();
    expect(screen.getByText('配方')).toBeInTheDocument();
    expect(screen.getByText('推导运行')).toBeInTheDocument();
    expect(screen.getByText('溯源图谱')).toBeInTheDocument();
  });

  it('shows evidence set data in the default tab', async () => {
    renderPage();
    expect(await screen.findByText('材料性能证据集')).toBeInTheDocument();
  });

  it('opens create evidence set modal when 新建证据集 clicked', async () => {
    renderPage();
    const createBtn = await screen.findByRole('button', { name: /新建证据集/ });
    await userEvent.click(createBtn);
    // The modal should show the form input with placeholder
    expect(screen.getByPlaceholderText('如：材料性能证据集')).toBeInTheDocument();
  });

  it('switches to recipes tab and shows recipe data', async () => {
    renderPage();
    await screen.findByText('材料性能证据集');
    await userEvent.click(screen.getByText('配方'));
    expect(await screen.findByText('屈服强度配方')).toBeInTheDocument();
  });

  it('switches to derivation runs tab and shows run data', async () => {
    renderPage();
    await screen.findByText('材料性能证据集');
    await userEvent.click(screen.getByText('推导运行'));
    expect(await screen.findByText('run-001')).toBeInTheDocument();
  });

  it('switches to graph tab and shows placeholder when no run selected', async () => {
    renderPage();
    await screen.findByText('材料性能证据集');
    await userEvent.click(screen.getByText('溯源图谱'));
    expect(await screen.findByText('请选择一个推导运行')).toBeInTheDocument();
  });

  it('renders graph nodes and edges when initialRunId provided', async () => {
    renderPage({ initialRunId: 'run-001' });
    // Graph tab should be active, and graph data should be loaded
    expect(await screen.findByText('事实版本1')).toBeInTheDocument();
    expect(screen.getByText('参数版本1')).toBeInTheDocument();
  });
});
