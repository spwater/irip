/**
 * ResearchShowcasePanel — 研究产物展示区（右栏）
 *
 * 替代原 AiAssistantPanel，展示研究分析的产物：
 * - Insight 候选卡片（接受/修改/拒绝）
 * - 已确认产物列表（Insight/Dataset/View，按类型分组）
 * - 产物详情视图（点击产物查看）
 */
import { useState, useEffect, useCallback } from 'react';
import { Card, Tag, Space, Button, Typography, Spin, Empty, Popconfirm, Modal, Input, message } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  BulbOutlined,
  DatabaseOutlined,
  BarChartOutlined,
  DeleteOutlined,
  CheckOutlined,
  CloseOutlined,
} from '@ant-design/icons';
import { apiListProducts, apiDeleteInsight, apiDeleteDataset, apiDeleteView, apiAcceptCandidate, apiRejectCandidate, apiRejectAnyCandidate, apiGetCandidates, apiCreateDataset, type ProductSummary, type CandidateProduct } from '@/api/researchProducts';
import type { InsightCandidate } from '@/api/research';
import { ProductDetailView } from './ProductDetailView';
import { PublishButton } from './PublishButton';

const { Text } = Typography;

export type ResearchShowcasePanelProps = {
  workspaceId: string;
  // Insight 候选（来自中栏分析流程）
  insightCandidate: InsightCandidate | null;
  insightCandidateId: string | null;
  insightRunId: string | null;
  onInsightAccepted: () => void;
  onInsightRejected: () => void;
  // 产物列表刷新触发
  productsRefresh: number;
  onProductsRefresh: () => void;
  // 最新 run ID（用于查候选）
  latestRunId?: string | null;
};

export function ResearchShowcasePanel({
  workspaceId,
  insightCandidate,
  insightCandidateId,
  insightRunId,
  onInsightAccepted,
  onInsightRejected,
  productsRefresh,
  onProductsRefresh,
  latestRunId,
}: ResearchShowcasePanelProps): JSX.Element {
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [selectedProduct, setSelectedProduct] = useState<{ type: string; id: string } | null>(null);
  const [datasetCandidates, setDatasetCandidates] = useState<CandidateProduct[]>([]);
  const [viewCandidates, setViewCandidates] = useState<CandidateProduct[]>([]);
  const [confirmingDataset, setConfirmingDataset] = useState(false);
  const [datasetModalOpen, setDatasetModalOpen] = useState(false);
  const [pendingCandidate, setPendingCandidate] = useState<CandidateProduct | null>(null);
  const [datasetName, setDatasetName] = useState('');

  const fetchProducts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListProducts(workspaceId);
      setProducts(res?.items ?? []);
    } catch (err) {
      console.error('加载已确认产物失败', err);
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void fetchProducts();
  }, [fetchProducts, productsRefresh]);

  // 获取候选产物（只查最新 run）
  useEffect(() => {
    if (!latestRunId) {
      setDatasetCandidates([]);
      setViewCandidates([]);
      return;
    }
    void (async () => {
      try {
        const res = await apiGetCandidates(workspaceId, latestRunId);
        const items = res.items ?? [];
        setDatasetCandidates(items.filter((c) => c.candidate_type === 'derived_dataset' && c.status === 'available'));
        setViewCandidates(items.filter((c) => c.candidate_type === 'view' && c.status === 'available'));
      } catch {
        message.error('加载候选产物失败');
        setDatasetCandidates([]);
        setViewCandidates([]);
      }
    })();
  }, [workspaceId, latestRunId, productsRefresh]);

  const handleConfirmDataset = useCallback((candidate: CandidateProduct) => {
    const preview = candidate.preview_data ?? {};
    const metaPreview = (preview as Record<string, Record<string, unknown>>).metadata_preview ?? {};
    const suggested = String(metaPreview.analysis_scope ?? '分析数据集');
    setPendingCandidate(candidate);
    setDatasetName(suggested.slice(0, 200));
    setDatasetModalOpen(true);
  }, []);

  const handleDatasetModalOk = useCallback(async () => {
    if (!pendingCandidate) return;
    if (!datasetName.trim()) {
      message.warning('请输入数据集名称');
      return;
    }
    setDatasetModalOpen(false);
    setConfirmingDataset(true);
    try {
      await apiCreateDataset(workspaceId, {
        artifact_id: pendingCandidate.source_artifact_id ?? '',
        name: datasetName.trim().slice(0, 200),
      });
      message.success('数据集已确认，已创建为正式产物');
      onProductsRefresh();
      setPendingCandidate(null);
    } catch {
      message.error('确认失败');
    } finally {
      setConfirmingDataset(false);
    }
  }, [pendingCandidate, datasetName, workspaceId, onProductsRefresh]);

  const handleAccept = useCallback(async () => {
    if (!insightCandidateId) return;
    setAccepting(true);
    try {
      await apiAcceptCandidate(workspaceId, insightRunId || '00000000-0000-0000-0000-000000000000', insightCandidateId);
      message.success('Insight 已接受，已创建为正式产物');
      onInsightAccepted();
      onProductsRefresh();
    } catch {
      message.error('接受失败');
    } finally {
      setAccepting(false);
    }
  }, [insightCandidateId, insightRunId, workspaceId, onInsightAccepted, onProductsRefresh]);

  const handleReject = useCallback(async () => {
    if (!insightCandidateId) return;
    try {
      await apiRejectCandidate(workspaceId, insightRunId || '00000000-0000-0000-0000-000000000000', insightCandidateId, '用户拒绝');
      message.success('已拒绝');
      onInsightRejected();
    } catch {
      message.error('操作失败');
    }
  }, [insightCandidateId, insightRunId, workspaceId, onInsightRejected]);

  const handleDelete = useCallback(async (productId: string, productType: string) => {
    setDeleting(productId);
    try {
      if (productType === 'insight') {
        await apiDeleteInsight(workspaceId, productId);
      } else if (productType === 'derived_dataset' || productType === 'dataset') {
        await apiDeleteDataset(workspaceId, productId);
      } else if (productType === 'view') {
        await apiDeleteView(workspaceId, productId);
      }
      message.success('已删除');
      await fetchProducts();
    } catch {
      message.error('删除失败');
    } finally {
      setDeleting(null);
    }
  }, [workspaceId, fetchProducts]);

  // 按类型分组
  const grouped: Record<string, ProductSummary[]> = {};
  for (const p of products) {
    if (!grouped[p.product_type]) grouped[p.product_type] = [];
    grouped[p.product_type].push(p);
  }

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

  return (
    <div style={{ height: '100%', overflowY: 'auto', padding: '12px' }}>
      {/* 产物详情视图（点击产物后展开） */}
      {selectedProduct && (
        <ProductDetailView
          workspaceId={workspaceId}
          productType={selectedProduct.type}
          productId={selectedProduct.id}
          onBack={() => setSelectedProduct(null)}
        />
      )}

      {!selectedProduct && (
        <>
      {/* Insight 候选 */}
      {insightCandidate && (
        <Card
          size="small"
          title={
            <Space>
              <BulbOutlined />
              <Text strong>Insight 候选</Text>
              <Tag color={insightCandidate.confidence_level === 'high' ? 'green' : insightCandidate.confidence_level === 'medium' ? 'orange' : 'red'}>
                {insightCandidate.confidence_level || 'unknown'}
              </Tag>
            </Space>
          }
          extra={
            insightCandidateId && (
              <Space size="small">
                <Button size="small" type="primary" icon={<CheckOutlined />} loading={accepting} onClick={handleAccept}>
                  接受
                </Button>
                <Button size="small" icon={<CloseOutlined />} onClick={() => message.info('修改功能开发中')}>
                  修改
                </Button>
                <Popconfirm title="确定拒绝此候选？" onConfirm={handleReject} okText="拒绝" cancelText="取消">
                  <Button size="small" danger icon={<CloseOutlined />}>拒绝</Button>
                </Popconfirm>
              </Space>
            )
          }
          style={{ marginBottom: 12 }}
        >
          {insightCandidate.conclusion && (
            <div style={{ marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>结论：</Text>
              <div className="research-markdown" style={{ fontSize: 13, marginTop: 2 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{insightCandidate.conclusion}</ReactMarkdown>
              </div>
            </div>
          )}
          {insightCandidate.scope && (
            <div style={{ marginBottom: 6 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>范围：</Text>
              <div className="research-markdown" style={{ fontSize: 12, marginTop: 2 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{insightCandidate.scope}</ReactMarkdown>
              </div>
            </div>
          )}
          {insightCandidate.limitations && (
            <div style={{ marginBottom: 6 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>局限：</Text>
              <div className="research-markdown" style={{ fontSize: 12, marginTop: 2 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{insightCandidate.limitations}</ReactMarkdown>
              </div>
            </div>
          )}
          {insightCandidate.evidence_source_label && (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>证据来源：</Text>
              <Tag>{insightCandidate.evidence_source_label}</Tag>
            </div>
          )}
        </Card>
      )}

      {/* DerivedDataset 候选 */}
      {datasetCandidates.length > 0 && (
        <Card
          size="small"
          title={
            <Space>
              <DatabaseOutlined />
              <Text strong>数据集候选</Text>
              <Tag color="blue">{datasetCandidates.length}</Tag>
            </Space>
          }
          style={{ marginBottom: 12 }}
        >
          {datasetCandidates.map((c) => {
            const preview = c.preview_data ?? {};
            const metaPreview = (preview as Record<string, Record<string, unknown>>).metadata_preview ?? {};
            const pointsPreview = (preview as Record<string, unknown[]>).points_preview ?? [];
            const seriesPreview = (preview as Record<string, unknown[]>).series_preview ?? [];
            return (
              <div key={c.candidate_id} style={{ marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid var(--ocean-border-subtle, #f0f0f0)' }}>
                <Space style={{ marginBottom: 4 }}>
                  <Text strong style={{ fontSize: 13 }}>
                    {String(metaPreview.analysis_scope ?? '分析数据集').slice(0, 60)}
                  </Text>
                </Space>
                {pointsPreview.length > 0 && (
                  <div style={{ marginBottom: 4 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>指标 ({pointsPreview.length}):</Text>
                    <Space size={4} wrap>
                      {pointsPreview.slice(0, 4).map((p, i) => {
                        const pt = p as Record<string, unknown>;
                        return <Tag key={i} style={{ fontSize: 10 }}>{String(pt.name ?? '')}: {String(pt.value ?? '')}{String(pt.unit ?? '')}</Tag>;
                      })}
                      {pointsPreview.length > 4 && <Tag style={{ fontSize: 10 }}>+{pointsPreview.length - 4}</Tag>}
                    </Space>
                  </div>
                )}
                {seriesPreview.length > 0 && (
                  <div style={{ marginBottom: 6 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>表格 ({seriesPreview.length}):</Text>
                    <Space size={4} wrap>
                      {seriesPreview.map((s, i) => {
                        const sr = s as Record<string, unknown>;
                        return <Tag key={i} style={{ fontSize: 10 }}>{String(sr.name ?? '')} ({String(sr.row_count ?? '?')}行)</Tag>;
                      })}
                    </Space>
                  </div>
                )}
                <Space>
                  <Button
                    size="small"
                    type="primary"
                    icon={<CheckOutlined />}
                    loading={confirmingDataset}
                    onClick={() => handleConfirmDataset(c)}
                  >
                    确认为数据集
                  </Button>
                  <Popconfirm
                    title="确定拒绝此候选？"
                    onConfirm={async () => {
                      try {
                        await apiRejectAnyCandidate(workspaceId, latestRunId || '', c.candidate_id, '用户拒绝');
                        setDatasetCandidates(prev => prev.filter(x => x.candidate_id !== c.candidate_id));
                      } catch {
                        message.error('拒绝失败，请重试');
                      }
                    }}
                    okText="拒绝"
                    cancelText="取消"
                  >
                    <Button size="small" danger icon={<CloseOutlined />}>拒绝</Button>
                  </Popconfirm>
                </Space>
              </div>
            );
          })}
        </Card>
      )}

      {/* 图表候选 */}
      {viewCandidates.length > 0 && (
        <Card
          size="small"
          title={
            <Space>
              <BarChartOutlined style={{ color: '#1890ff' }} />
              <Typography.Text strong>图表候选</Typography.Text>
              <Tag color="cyan">{viewCandidates.length}</Tag>
            </Space>
          }
          style={{ marginBottom: 12 }}
        >
          {viewCandidates.map((c) => {
            const preview = c.preview_data ?? {};
            const title = String((preview as Record<string, unknown>).title ?? `图表 ${c.candidate_id.slice(0, 6)}`);
            return (
              <div
                key={c.candidate_id}
                style={{
                  padding: '6px 0',
                  borderBottom: '1px dashed #f0f0f0',
                }}
              >
                <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Text style={{ fontSize: 13 }} ellipsis={{ tooltip: title }}>{title}</Text>
                  <Space>
                    <Button
                      size="small"
                      type="primary"
                      loading={confirmingDataset}
                      onClick={async () => {
                        setConfirmingDataset(true);
                        try {
                          const { apiCreateView } = await import('@/api/researchProducts');
                          await apiCreateView(workspaceId, { artifact_id: c.source_artifact_id ?? '', name: title.slice(0, 200) });
                          message.success('图表已确认，已创建为正式产物');
                          onProductsRefresh();
                          setViewCandidates(prev => prev.filter(x => x.candidate_id !== c.candidate_id));
                        } catch {
                          message.error('确认失败');
                        } finally {
                          setConfirmingDataset(false);
                        }
                      }}
                    >
                      确认为图表
                    </Button>
                    <Popconfirm
                      title="确定拒绝此候选？"
                      onConfirm={async () => {
                        try {
                          await apiRejectAnyCandidate(workspaceId, latestRunId || '', c.candidate_id, '用户拒绝');
                          setViewCandidates(prev => prev.filter(x => x.candidate_id !== c.candidate_id));
                        } catch {
                          message.error('拒绝失败，请重试');
                        }
                      }}
                      okText="拒绝"
                      cancelText="取消"
                    >
                      <Button size="small" danger icon={<CloseOutlined />}>拒绝</Button>
                    </Popconfirm>
                  </Space>
                </Space>
              </div>
            );
          })}
        </Card>
      )}

      {/* 已确认产物 */}
      {loading ? (
        <Card size="small" title="研究产物" style={{ marginBottom: 12 }}>
          <div style={{ textAlign: 'center', padding: 12 }}><Spin size="small" /></div>
        </Card>
      ) : products.length > 0 ? (
        <Card
          size="small"
          title={`研究产物 (${products.length})`}
          style={{ marginBottom: 12 }}
        >
          {Object.entries(grouped).map(([type, items]) => (
            <div key={type} style={{ marginBottom: 8 }}>
              <Text strong style={{ fontSize: 13 }}>
                {TYPE_ICONS[type]} {TYPE_LABELS[type] ?? type} ({items.length})
              </Text>
              {items.map((item) => (
                <div
                  key={item.product_id}
                  role="button"
                  tabIndex={0}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '4px 0',
                    cursor: 'pointer',
                  }}
                  onClick={() => setSelectedProduct({ type: item.product_type, id: item.product_id })}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setSelectedProduct({ type: item.product_type, id: item.product_id });
                    }
                  }}
                >
                  <Space style={{ flex: 1, minWidth: 0 }}>
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
                      onConfirm={(e) => { e?.stopPropagation(); handleDelete(item.product_id, item.product_type); }}
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
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Popconfirm>
                  )}
                  {(item.product_type === 'derived_dataset' || item.product_type === 'dataset' || item.product_type === 'view') && (
                    <Popconfirm
                      title="确定删除此产物？"
                      description="删除后不可恢复"
                      onConfirm={(e) => { e?.stopPropagation(); handleDelete(item.product_id, item.product_type); }}
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
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Popconfirm>
                  )}
                </div>
              ))}
            </div>
          ))}
        </Card>
      ) : null}

      {/* 发布按钮（在已确认产物列表下方） */}
      {products.length > 0 && (
        <PublishButton
          workspaceId={workspaceId}
          products={products}
          onPublished={() => {
            onProductsRefresh();
          }}
        />
      )}

      {/* 空状态 */}
      {!insightCandidate && products.length === 0 && !loading && (
        <Empty
          description="暂无研究产物"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ marginTop: 48 }}
        />
      )}
        </>
      )}

      {/* 数据集命名弹窗 */}
      <Modal
        title="确认为数据集"
        open={datasetModalOpen}
        onOk={handleDatasetModalOk}
        onCancel={() => { setDatasetModalOpen(false); setPendingCandidate(null); }}
        okText="确认"
        cancelText="取消"
        confirmLoading={confirmingDataset}
        width={480}
      >
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            请为数据集命名（可修改默认值）：
          </Text>
        </div>
        <Input
          value={datasetName}
          onChange={(e) => setDatasetName(e.target.value)}
          placeholder="输入数据集名称"
          maxLength={200}
        />
      </Modal>
    </div>
  );
}
