/**
 * ConfirmedProductsPanel — 已确认产物列表
 *
 * 按类型分组展示已确认产物
 */
import { useState, useEffect, useCallback } from 'react';
import { Card, List, Tag, Typography, Spin, Space, Button, Popconfirm, message } from 'antd';
import {
  DatabaseOutlined,
  BarChartOutlined,
  BulbOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import { apiListProducts, apiDeleteInsight, type ProductSummary } from '@/api/researchProducts';

const { Text } = Typography;

const TYPE_ICONS: Record<string, React.ReactNode> = {
  derived_dataset: <DatabaseOutlined />,
  view: <BarChartOutlined />,
  insight: <BulbOutlined />,
};

const TYPE_LABELS: Record<string, string> = {
  derived_dataset: '数据集',
  view: '视图',
  insight: 'Insight',
};

export type ConfirmedProductsPanelProps = {
  workspaceId: string;
  onSelectProduct?: (productType: string, productId: string) => void;
  refreshTrigger?: number;
};

export function ConfirmedProductsPanel({
  workspaceId,
  onSelectProduct,
  refreshTrigger,
}: ConfirmedProductsPanelProps): JSX.Element | null {
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const fetchProducts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListProducts(workspaceId);
      setProducts(res?.items ?? []);
    } catch {
      message.error('加载已确认产物失败');
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void fetchProducts();
  }, [fetchProducts, refreshTrigger]);

  const handleDelete = useCallback(async (productId: string) => {
    setDeleting(productId);
    try {
      await apiDeleteInsight(workspaceId, productId);
      message.success('已删除');
      await fetchProducts();
    } catch {
      message.error('删除失败');
    } finally {
      setDeleting(null);
    }
  }, [workspaceId, fetchProducts]);

  if (loading) {
    return (
      <Card size="small" title="已确认产物" style={{ marginBottom: 12 }}>
        <div style={{ textAlign: 'center', padding: 12 }}>
          <Spin size="small" />
        </div>
      </Card>
    );
  }

  if (products.length === 0) {
    return null;
  }

  // 按类型分组
  const grouped: Record<string, ProductSummary[]> = {};
  for (const p of products) {
    if (!grouped[p.product_type]) grouped[p.product_type] = [];
    grouped[p.product_type].push(p);
  }

  return (
    <Card size="small" title={`已确认产物 (${products.length})`} style={{ marginBottom: 12 }}>
      {Object.entries(grouped).map(([type, items]) => (
        <div key={type} style={{ marginBottom: 8 }}>
          <Text strong style={{ fontSize: 13 }}>
            {TYPE_ICONS[type]} {TYPE_LABELS[type] ?? type} ({items.length})
          </Text>
          <List
            size="small"
            dataSource={items}
            renderItem={(item) => (
              <List.Item
                style={{ padding: '4px 0' }}
              >
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Space
                    style={{ cursor: 'pointer', flex: 1, minWidth: 0 }}
                    onClick={() => onSelectProduct?.(item.product_type, item.product_id)}
                  >
                    <Text
                      style={{ fontSize: 12 }}
                      ellipsis={{ tooltip: item.name }}
                    >
                      {item.name}
                    </Text>
                    <Tag style={{ fontSize: 10 }}>v{item.current_version}</Tag>
                  </Space>
                  {item.product_type === 'insight' && (
                    <Popconfirm
                      title="确定删除此 Insight？"
                      description="删除后不可恢复"
                      onConfirm={() => handleDelete(item.product_id)}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                    >
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        loading={deleting === item.product_id}
                      />
                    </Popconfirm>
                  )}
                </Space>
              </List.Item>
            )}
          />
        </div>
      ))}
    </Card>
  );
}
