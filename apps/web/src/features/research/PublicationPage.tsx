/**
 * PublicationPage — 发布成果页（Tab 激活）
 *
 * 三种视图（全部成果 / 我发布的 / 我收藏的）+ 搜索 + 筛选 + 成果包卡片列表 + 分页
 */
import { useCallback, useEffect, useState } from 'react';
import { Row, Col, Empty, Spin, Pagination, Segmented, message, Button } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import {
  apiSearchPublications,
  apiAddFavorite,
  apiRemoveFavorite,
  apiGetPublicationDetail,
  type SearchResultItem,
  type ResultDetail,
  type SearchResultPage,
} from '@/api/researchPublish';
import { ResultSearchBar, type SearchResultFilters } from './ResultSearchBar';
import { ResultCard } from './ResultCard';
import { ResultDetailView } from './ResultDetailView';

type ViewMode = 'all' | 'mine' | 'favorites';

const PAGE_SIZE = 12;

const DEFAULT_FILTERS: SearchResultFilters = {
  query: '',
  tags: '',
  date_from: null,
  date_to: null,
  data_type: null,
  semantic: false,
};

export function PublicationPage(): JSX.Element {
  const [viewMode, setViewMode] = useState<ViewMode>('all');
  const [filters, setFilters] = useState<SearchResultFilters>(DEFAULT_FILTERS);
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<ResultDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [favSet, setFavSet] = useState<Set<string>>(new Set());
  const [aclLoadingId, setAclLoadingId] = useState<string | null>(null);

  const handleAclChange = useCallback(async (resultId: string, acl: string) => {
    setAclLoadingId(resultId);
    try {
      const item = results.find(r => r.result_id === resultId);
      if (!item) return;
      const { apiUpdateAcl } = await import('@/api/researchPublish');
      await apiUpdateAcl(item.workspace_id, resultId, { acl_type: acl });
      setResults(prev => prev.map(r => r.result_id === resultId ? { ...r, current_acl_type: acl } : r));
    } catch {
      // ignore
    } finally {
      setAclLoadingId(null);
    }
  }, [results]);

  const fetchResults = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = {
        view_mode: viewMode,
        page,
        page_size: PAGE_SIZE,
      };
      if (filters.query.trim()) params.query = filters.query.trim();
      if (filters.tags.trim()) params.tags = filters.tags.trim();
      if (filters.date_from) params.date_from = filters.date_from;
      if (filters.date_to) params.date_to = filters.date_to;
      if (filters.data_type) params.data_type = filters.data_type;

      const res: SearchResultPage = await apiSearchPublications(
        params as Parameters<typeof apiSearchPublications>[0],
      );
      setResults(res.items ?? []);
      setTotal(res.total ?? 0);
      // 从结果中提取收藏状态
      const favIds = new Set<string>();
      // favorites 视图下全部已收藏
      if (viewMode === 'favorites') {
        (res.items ?? []).forEach((item) => favIds.add(item.result_id));
      }
      setFavSet(favIds);
    } catch {
      message.error('加载成果包列表失败');
      setResults([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [viewMode, page, filters]);

  useEffect(() => {
    void fetchResults();
  }, [fetchResults]);

  const handleSearch = useCallback(() => {
    setPage(1);
    void fetchResults();
  }, [fetchResults]);

  const handleReset = useCallback(() => {
    setFilters(DEFAULT_FILTERS);
    setPage(1);
  }, []);

  const handleViewModeChange = useCallback((val: string) => {
    setViewMode(val as ViewMode);
    setPage(1);
    setSelectedResultId(null);
    setSelectedDetail(null);
  }, []);

  const handleResultClick = useCallback(async (resultId: string) => {
    setSelectedResultId(resultId);
    setDetailLoading(true);
    try {
      const detail = await apiGetPublicationDetail(resultId);
      setSelectedDetail(detail);
      if (detail.is_favorited) {
        setFavSet((prev) => new Set(prev).add(resultId));
      } else {
        setFavSet((prev) => {
          const next = new Set(prev);
          next.delete(resultId);
          return next;
        });
      }
    } catch {
      message.error('加载成果包详情失败');
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const handleFavoriteToggle = useCallback(
    async (resultId: string) => {
      const isFav = favSet.has(resultId);
      try {
        if (isFav) {
          await apiRemoveFavorite(resultId);
          setFavSet((prev) => {
            const next = new Set(prev);
            next.delete(resultId);
            return next;
          });
          message.success('已取消收藏');
        } else {
          await apiAddFavorite(resultId);
          setFavSet((prev) => new Set(prev).add(resultId));
          message.success('已收藏');
        }
      } catch {
        message.error('操作失败');
      }
    },
    [favSet],
  );

  const handleBack = useCallback(() => {
    setSelectedResultId(null);
    setSelectedDetail(null);
    void fetchResults();
  }, [fetchResults]);

  // ---- 详情视图 ----
  if (selectedResultId) {
    if (detailLoading) {
      return (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      );
    }
    if (!selectedDetail) {
      return (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Empty description="成果包不存在或无访问权限" />
          <Button icon={<ArrowLeftOutlined />} onClick={handleBack} style={{ marginTop: 16 }}>
            返回列表
          </Button>
        </div>
      );
    }
    return (
      <div style={{ padding: 16 }}>
        <ResultDetailView
          resultId={selectedResultId}
          detail={selectedDetail}
          isFavorited={favSet.has(selectedResultId)}
          onBack={handleBack}
          onFavoriteToggle={() => handleFavoriteToggle(selectedResultId)}
        />
      </div>
    );
  }

  // ---- 列表视图 ----
  return (
    <div style={{ padding: 24 }}>
      {/* 视图切换 + 搜索栏 */}
      <div style={{ marginBottom: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Segmented
          value={viewMode}
          onChange={handleViewModeChange}
          options={[
            { label: '全部成果', value: 'all' },
            { label: '我发布的', value: 'mine' },
            { label: '我收藏的', value: 'favorites' },
          ]}
        />
        <ResultSearchBar
          filters={filters}
          onChange={setFilters}
          onSearch={handleSearch}
          onReset={handleReset}
        />
      </div>

      {/* 结果列表 */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      ) : results.length === 0 ? (
        <Empty
          description={
            viewMode === 'favorites'
              ? '暂无收藏的成果包'
              : viewMode === 'mine'
                ? '您还未发布任何成果包'
                : '暂无已发布成果包'
          }
          style={{ padding: 60 }}
        />
      ) : (
        <>
          <Row gutter={[16, 16]}>
            {results.map((item) => (
              <Col key={item.result_id} xs={24} sm={12} md={8} lg={6}>
                <ResultCard
                  item={item}
                  isFavorited={favSet.has(item.result_id)}
                  onClick={() => handleResultClick(item.result_id)}
                  onFavoriteToggle={() => handleFavoriteToggle(item.result_id)}
                  onAclChange={handleAclChange}
                  aclLoading={aclLoadingId === item.result_id}
                />
              </Col>
            ))}
          </Row>
          {total > PAGE_SIZE && (
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: 24 }}>
              <Pagination
                current={page}
                total={total}
                pageSize={PAGE_SIZE}
                onChange={(p) => setPage(p)}
                showSizeChanger={false}
                showTotal={(t) => `共 ${t} 个成果包`}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
