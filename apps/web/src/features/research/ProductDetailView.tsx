/**
 * ProductDetailView — 产物详情视图
 *
 * 根据类型渲染 DatasetPreview / ViewPreview / InsightDetailView
 * 阶段 5 新增：底部集成"数据溯源"区域（ProductProvenanceSection）
 */
import { useState, useCallback } from 'react';
import { Button, Typography, Space, Card, Divider } from 'antd';
import { ArrowLeftOutlined, NodeIndexOutlined } from '@ant-design/icons';
import { DatasetPreview } from './DatasetPreview';
import { ViewPreview } from './ViewPreview';
import { InsightDetailView } from './InsightDetailView';
import { ProvenanceTab } from './ProvenanceTab';
import {
  apiQueryDatasetProvenance,
  apiQueryViewProvenance,
  apiQueryInsightProvenance,
} from '@/api/researchLineage';

const { Text } = Typography;

export type ProductDetailViewProps = {
  workspaceId: string;
  productType: string;
  productId: string;
  onBack: () => void;
};

export function ProductDetailView({
  workspaceId,
  productType,
  productId,
  onBack,
}: ProductDetailViewProps): JSX.Element {
  const [refreshKey] = useState(0);

  // ---- 产物溯源图查询函数 ----
  const fetchProvenance = useCallback(
    async (maxDepth: number) => {
      // 根据产物类型调用对应的溯源图查询便捷端点
      // version_number 传 1（获取最新版本的溯源图）
      if (productType === 'derived_dataset') {
        return apiQueryDatasetProvenance(productId, 1, maxDepth);
      }
      if (productType === 'view') {
        return apiQueryViewProvenance(productId, 1, maxDepth);
      }
      if (productType === 'insight') {
        return apiQueryInsightProvenance(productId, 1, maxDepth);
      }
      // 兜底：返回空图
      return {
        nodes: [],
        edges: [],
        stats: { total_nodes: 0, nodes_by_type: {}, restricted_nodes_count: 0, truncated_count: 0 },
      };
    },
    [productType, productId],
  );

  return (
    <div style={{ padding: '16px', height: '100%', overflowY: 'auto' }}>
      <Button
        type="link"
        icon={<ArrowLeftOutlined />}
        onClick={onBack}
        style={{ marginBottom: 8, paddingLeft: 0 }}
      >
        返回
      </Button>

      {productType === 'derived_dataset' && (
        <DatasetPreview
          key={`ds-${refreshKey}`}
          workspaceId={workspaceId}
          datasetId={productId}
        />
      )}

      {productType === 'view' && (
        <ViewPreview
          key={`vw-${refreshKey}`}
          workspaceId={workspaceId}
          viewId={productId}
        />
      )}

      {productType === 'insight' && (
        <InsightDetailView
          key={`ins-${refreshKey}`}
          workspaceId={workspaceId}
          insightId={productId}
        />
      )}

      {/* 数据溯源区域（阶段 5 新增） */}
      <Divider />
      <Card
        size="small"
        title={
          <Space>
            <NodeIndexOutlined style={{ color: 'var(--ocean-current-bright, #17b8ce)' }} />
            <Text strong>数据溯源</Text>
          </Space>
        }
        style={{ marginTop: 12 }}
      >
        <ProvenanceTab
          fetchGraph={fetchProvenance}
          title="产物溯源"
          height={400}
        />
      </Card>
    </div>
  );
}
