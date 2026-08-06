/**
 * ProductDetailView — 产物详情视图
 *
 * 根据类型渲染 DatasetPreview / ViewPreview / InsightDetailView
 */
import { useState } from 'react';
import { Button, Typography, Space, Tag } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { DatasetPreview } from './DatasetPreview';
import { ViewPreview } from './ViewPreview';
import { InsightDetailView } from './InsightDetailView';

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
  const [refreshKey, setRefreshKey] = useState(0);

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
    </div>
  );
}
