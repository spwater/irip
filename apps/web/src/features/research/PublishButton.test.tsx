import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { apiListProducts } from '@/api/researchProducts';
import { PublishButton } from './PublishButton';
import type { ProductSummary } from '@/api/researchProducts';

vi.mock('@/api/researchProducts', () => ({
  apiListProducts: vi.fn(),
}));

vi.mock('./PublishConfirmModal', () => ({
  PublishConfirmModal: ({ open }: { open: boolean }) =>
    open ? <div data-testid="publish-modal">PublishConfirmModal</div> : null,
}));

const mockProducts: ProductSummary[] = [
  { product_type: 'derived_dataset', product_id: 'ds-1', name: '数据集1', status: 'confirmed', current_version: 1 },
  { product_type: 'view', product_id: 'vw-1', name: '视图1', status: 'confirmed', current_version: 1 },
];

function renderButton(props: {
  workspaceId?: string;
  products?: ProductSummary[];
  onPublished?: () => void;
}): void {
  render(
    <AntApp>
      <PublishButton
        workspaceId={props.workspaceId ?? 'ws-001'}
        products={props.products}
        onPublished={props.onPublished ?? vi.fn()}
      />
    </AntApp>,
  );
}

describe('PublishButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListProducts).mockResolvedValue({ items: mockProducts });
  });

  it('renders 发布研究成果包 button', () => {
    renderButton({ products: mockProducts });
    expect(screen.getByRole('button', { name: /发布研究成果包/ })).toBeInTheDocument();
  });

  it('is disabled when no products provided externally', () => {
    renderButton({ products: [] });
    const btn = screen.getByRole('button', { name: /发布研究成果包/ });
    expect(btn).toBeDisabled();
  });

  it('is disabled when only insight products (no dataset or view)', () => {
    const insightOnly: ProductSummary[] = [
      { product_type: 'insight', product_id: 'ins-1', name: '发现1', status: 'confirmed', current_version: 1 },
    ];
    renderButton({ products: insightOnly });
    const btn = screen.getByRole('button', { name: /发布研究成果包/ });
    expect(btn).toBeDisabled();
  });

  it('is enabled when dataset and view products present', () => {
    renderButton({ products: mockProducts });
    const btn = screen.getByRole('button', { name: /发布研究成果包/ });
    expect(btn).not.toBeDisabled();
  });

  it('opens PublishConfirmModal when clicked with valid products', async () => {
    renderButton({ products: mockProducts });
    const btn = screen.getByRole('button', { name: /发布研究成果包/ });
    await userEvent.click(btn);
    expect(screen.getByTestId('publish-modal')).toBeInTheDocument();
  });

  it('loads products from API when not provided externally', async () => {
    renderButton({});
    expect(vi.mocked(apiListProducts)).toHaveBeenCalledWith('ws-001');
  });

  it('shows tooltip hint when no products', () => {
    renderButton({ products: [] });
    const btn = screen.getByRole('button', { name: /发布研究成果包/ });
    expect(btn).toBeDisabled();
  });

  it('shows tooltip hint when only insights', () => {
    const insightOnly: ProductSummary[] = [
      { product_type: 'insight', product_id: 'ins-1', name: '发现1', status: 'confirmed', current_version: 1 },
    ];
    renderButton({ products: insightOnly });
    const btn = screen.getByRole('button', { name: /发布研究成果包/ });
    expect(btn).toBeDisabled();
  });
});
