import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { apiPublishResult } from '@/api/researchPublish';
import { PublishConfirmModal } from './PublishConfirmModal';
import type { ProductSummary } from '@/api/researchProducts';

vi.mock('@/api/researchPublish', () => ({
  apiPublishResult: vi.fn(),
}));

const mockProducts: ProductSummary[] = [
  { product_type: 'derived_dataset', product_id: 'ds-1', name: '烧结数据集', status: 'confirmed', current_version: 1 },
  { product_type: 'view', product_id: 'vw-1', name: '趋势视图', status: 'confirmed', current_version: 2 },
  { product_type: 'insight', product_id: 'ins-1', name: '关键发现', status: 'confirmed', current_version: 1 },
];

function renderModal(props: {
  open?: boolean;
  workspaceId?: string;
  products?: ProductSummary[];
  onClose?: () => void;
  onPublished?: (ref: unknown) => void;
}): void {
  render(
    <AntApp>
      <PublishConfirmModal
        open={props.open ?? true}
        workspaceId={props.workspaceId ?? 'ws-001'}
        products={props.products ?? mockProducts}
        onClose={props.onClose ?? vi.fn()}
        onPublished={props.onPublished ?? vi.fn()}
      />
    </AntApp>,
  );
}

describe('PublishConfirmModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiPublishResult).mockResolvedValue({
      result_id: 'r-001',
      version_number: 1,
      title: '测试成果包',
      status: 'published',
      published_at: '2025-01-01T00:00:00Z',
    });
  });

  it('renders modal title 发布研究成果包 when open', () => {
    renderModal({});
    expect(screen.getByText('发布研究成果包')).toBeInTheDocument();
  });

  it('renders product list with data type, view, and insight sections', () => {
    renderModal({});
    // Product section labels include count; data type Select also has these labels
    const datasetElements = screen.getAllByText(/数据集/);
    expect(datasetElements.length).toBeGreaterThanOrEqual(1);
    const viewElements = screen.getAllByText(/视图/);
    expect(viewElements.length).toBeGreaterThanOrEqual(1);
    const insightElements = screen.getAllByText(/Insight/);
    expect(insightElements.length).toBeGreaterThanOrEqual(1);
  });

  it('shows selected count text', () => {
    renderModal({});
    expect(screen.getByText(/已选\s*3\s*\/\s*3\s*个产物/)).toBeInTheDocument();
  });

  it('renders title input field', () => {
    renderModal({});
    expect(screen.getByPlaceholderText('为成果包命名…')).toBeInTheDocument();
  });

  it('renders ACL select with options', () => {
    renderModal({});
    // The default ACL value is 'private', shown in the select
    expect(screen.getByText('私有（仅自己可见）')).toBeInTheDocument();
  });

  it('disables publish button when title is empty', () => {
    renderModal({});
    const publishBtn = screen.getByRole('button', { name: /发布\s*\(/ });
    expect(publishBtn).toBeDisabled();
  });

  it('enables publish button when title entered and products selected', async () => {
    renderModal({});
    const titleInput = screen.getByPlaceholderText('为成果包命名…');
    await userEvent.type(titleInput, '烧结研究成果包');
    const publishBtn = screen.getByRole('button', { name: /发布\s*\(/ });
    expect(publishBtn).not.toBeDisabled();
  });

  it('renders warning alert when no products', () => {
    renderModal({ products: [] });
    expect(screen.getByText('暂无已确认产物，请先在 Workspace 中确认产物')).toBeInTheDocument();
  });

  it('renders 溯源引用 section', () => {
    renderModal({});
    expect(screen.getByText('溯源引用')).toBeInTheDocument();
  });

  it('toggles product checkbox when clicked', async () => {
    renderModal({});
    const checkboxes = screen.getAllByRole('checkbox');
    // First checkbox should be checked (selected by default)
    expect(checkboxes[0]).toBeChecked();
    await userEvent.click(checkboxes[0]);
    expect(checkboxes[0]).not.toBeChecked();
  });
});
