/**
 * PublishButton — Workspace 内发布入口按钮
 *
 * 在已确认产物列表下方显示"发布研究成果包"按钮
 * 点击后打开 PublishConfirmModal
 */
import { useState, useCallback, useEffect } from 'react';
import { Button, Badge, Tooltip, message } from 'antd';
import { ExportOutlined } from '@ant-design/icons';
import type { ProductSummary } from '@/api/researchProducts';
import { apiListProducts } from '@/api/researchProducts';
import { PublishConfirmModal } from './PublishConfirmModal';

export type PublishButtonProps = {
  workspaceId: string;
  /** 外部传入的产物列表（如果已有则复用，否则组件内部加载） */
  products?: ProductSummary[];
  /** 产物列表刷新触发器 */
  refreshTrigger?: number;
  /** 发布成功后回调 */
  onPublished?: () => void;
};

export function PublishButton({
  workspaceId,
  products: externalProducts,
  refreshTrigger,
  onPublished,
}: PublishButtonProps): JSX.Element {
  const [modalOpen, setModalOpen] = useState(false);
  const [products, setProducts] = useState<ProductSummary[]>(externalProducts ?? []);
  const [loading, setLoading] = useState(false);

  const fetchProducts = useCallback(async () => {
    if (externalProducts) return; // 外部已传入则不重复加载
    setLoading(true);
    try {
      const res = await apiListProducts(workspaceId);
      setProducts(res?.items ?? []);
    } catch {
      // 静默
    } finally {
      setLoading(false);
    }
  }, [workspaceId, externalProducts]);

  // 当未提供外部产物列表时，监听 refreshTrigger 加载
  useEffect(() => {
    if (!externalProducts) {
      void fetchProducts();
    }
  }, [fetchProducts, refreshTrigger, externalProducts]);

  const handleClick = useCallback(async () => {
    if (!externalProducts) {
      await fetchProducts();
    }
    const confirmed = externalProducts ?? products;
    if (confirmed.length === 0) {
      message.warning('请先确认至少一个产物');
      return;
    }
    const hasDatasetOrView = confirmed.some(
      (p) => p.product_type === 'derived_dataset' || p.product_type === 'view',
    );
    if (!hasDatasetOrView) {
      message.warning('发布成果包至少需要包含一个数据集或视图，Insight 不能单独发布');
      return;
    }
    setModalOpen(true);
  }, [externalProducts, products, fetchProducts]);

  const handlePublished = useCallback(() => {
    setModalOpen(false);
    onPublished?.();
  }, [onPublished]);

  const displayProducts = externalProducts ?? products;
  const confirmedCount = displayProducts.length;
  const hasDatasetOrView = displayProducts.some(
    (p) => p.product_type === 'derived_dataset' || p.product_type === 'view',
  );

  return (
    <>
      <Tooltip
        title={
          confirmedCount === 0
            ? '请先确认产物'
            : !hasDatasetOrView
              ? '发布成果包至少需要包含一个数据集或视图'
              : `发布 ${confirmedCount} 个已确认产物为研究成果包`
        }
      >
        <Button
          type="primary"
          ghost
          icon={<ExportOutlined />}
          onClick={handleClick}
          loading={loading}
          block
          disabled={confirmedCount === 0 || !hasDatasetOrView}
          style={{ marginTop: 8 }}
        >
          发布研究成果包
          {confirmedCount > 0 && (
            <Badge
              count={confirmedCount}
              style={{ marginLeft: 8, backgroundColor: 'var(--ocean-accent, #1689ae)' }}
            />
          )}
        </Button>
      </Tooltip>

      <PublishConfirmModal
        open={modalOpen}
        workspaceId={workspaceId}
        products={displayProducts}
        onClose={() => setModalOpen(false)}
        onPublished={handlePublished}
      />
    </>
  );
}
