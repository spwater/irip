/**
 * KnowledgeSearchPanel — 知识库检索面板
 *
 * 功能：
 * - 搜索框（输入检索查询字符串）
 * - Provider 选择（可选指定 Provider 或全部并行检索）
 * - 结果列表（展示检索结果：标题 / 版本 / 相关度 / 段落摘要 / 来源链接）
 * - 引用保存提示（展示检索到的文献可作为知识引用快照保存）
 *
 * 参照 PRD 6.8 节 KnowledgeProvider 接口与 arch-research-lineage.md 2.3 节文件。
 */
import { useState, useCallback } from 'react';
import {
  Card,
  Input,
  Button,
  Spin,
  Empty,
  Typography,
  Tag,
  Space,
  List,
  Tooltip,
  Rate,
  message,
} from 'antd';
import {
  SearchOutlined,
  LinkOutlined,
  BookOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import type { KnowledgeSearchResult } from '@/api/researchLineage';
import { apiSearchKnowledge } from '@/api/researchLineage';

const { Text, Paragraph } = Typography;

// ============================================================
// Props
// ============================================================

export type KnowledgeSearchPanelProps = {
  /** 默认 Provider 名称（可选） */
  defaultProvider?: string;
  /** 容器样式 */
  style?: React.CSSProperties;
  /** 选中结果回调（可选） */
  onResultSelect?: (result: KnowledgeSearchResult) => void;
  /** 是否显示 Provider 选择器 */
  showProviderSelect?: boolean;
};

// ============================================================
// 组件
// ============================================================

/**
 * KnowledgeSearchPanel — 知识库检索面板
 *
 * 用户输入检索关键词后调用知识库检索 API，展示匹配结果列表。
 * 默认仅向知识库发送研究问题和用户确认的关键词（不发送完整 Fact 原始数据）。
 */
export function KnowledgeSearchPanel({
  defaultProvider,
  style,
  onResultSelect,
  showProviderSelect = true,
}: KnowledgeSearchPanelProps): JSX.Element {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<KnowledgeSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  // ---- 执行检索 ----
  const handleSearch = useCallback(async () => {
    if (!query.trim()) {
      message.warning('请输入检索关键词');
      return;
    }
    setLoading(true);
    setSearched(true);
    try {
      const res = await apiSearchKnowledge({
        search_query: query.trim(),
        provider_name: defaultProvider,
        max_results: 10,
      });
      setResults(res);
    } catch {
      message.error('知识库检索失败');
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [query, defaultProvider]);

  // ---- 按下回车搜索 ----
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void handleSearch();
      }
    },
    [handleSearch],
  );

  // ---- 选中结果 ----
  const handleSelectResult = useCallback(
    (result: KnowledgeSearchResult) => {
      if (onResultSelect) {
        onResultSelect(result);
      } else {
        message.info(`已选中: ${result.title}`);
      }
    },
    [onResultSelect],
  );

  return (
    <Card
      size="small"
      title={
        <Space>
          <BookOutlined style={{ color: 'var(--ocean-current-bright, #17b8ce)' }} />
          <Text strong>知识库检索</Text>
        </Space>
      }
      style={{
        marginBottom: 12,
        background: 'var(--ocean-surface-default, rgba(240,250,251,0.72))',
        border: '1px solid var(--ocean-border-subtle, rgba(24,102,133,0.16))',
        borderRadius: 'var(--ocean-radius-md, 6px)',
        ...style,
      }}
    >
      {/* 搜索框 */}
      <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
        <Input
          prefix={<SearchOutlined style={{ color: '#bfbfbf' }} />}
          placeholder="输入检索关键词或研究问题..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          allowClear
        />
        <Button
          type="primary"
          onClick={handleSearch}
          loading={loading}
          style={{ borderRadius: '0 6px 6px 0' }}
        >
          检索
        </Button>
      </Space.Compact>

      {/* Provider 提示 */}
      {showProviderSelect && defaultProvider && (
        <div style={{ marginBottom: 8 }}>
          <Tag icon={<ApiOutlined />} color="blue" style={{ fontSize: 10 }}>
            Provider: {defaultProvider}
          </Tag>
        </div>
      )}

      {/* 安全提示 */}
      <Text
        type="secondary"
        style={{ fontSize: 10, display: 'block', marginBottom: 8, fontStyle: 'italic' }}
      >
        * 仅发送研究问题和关键词，不发送完整实验数据
      </Text>

      {/* 结果列表 */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin tip="检索中..." />
        </div>
      ) : !searched ? (
        <Empty
          description="输入关键词开始检索"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ padding: 12 }}
        />
      ) : results.length === 0 ? (
        <Empty
          description="未找到匹配文献"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ padding: 12 }}
        />
      ) : (
        <List
          size="small"
          dataSource={results}
          renderItem={(result, idx) => {
            // 位置信息
            const locationParts: string[] = [];
            if (result.section) locationParts.push(`Section ${result.section}`);
            if (result.page) locationParts.push(`Page ${result.page}`);
            if (result.chunk_id) locationParts.push(`Chunk ${result.chunk_id}`);
            const locationStr = locationParts.join(', ');

            return (
              <List.Item
                key={`${result.document_id}-${idx}`}
                style={{
                  padding: '8px 4px',
                  cursor: 'pointer',
                  borderBottom: '1px solid var(--ocean-border-split, rgba(24,102,133,0.14))',
                }}
                onClick={() => handleSelectResult(result)}
              >
                <div style={{ width: '100%' }}>
                  {/* 标题行 */}
                  <Space
                    style={{
                      width: '100%',
                      justifyContent: 'space-between',
                      marginBottom: 2,
                    }}
                  >
                    <Space size={4}>
                      <Text strong style={{ fontSize: 12 }}>
                        {result.title || result.document_id}
                      </Text>
                      <Tag color="purple" style={{ fontSize: 10 }}>
                        v{result.document_version}
                      </Tag>
                    </Space>
                    <Tooltip title={`相关度: ${(result.relevance_score * 100).toFixed(0)}%`}>
                      <Rate
                        count={5}
                        value={Math.round(result.relevance_score * 5)}
                        disabled
                        style={{ fontSize: 10 }}
                      />
                    </Tooltip>
                  </Space>

                  {/* 段落摘要 */}
                  {result.snippet && (
                    <Paragraph
                      style={{
                        fontSize: 11,
                        margin: '2px 0',
                        color: 'var(--ocean-text-secondary, #486b7e)',
                      }}
                      ellipsis={{ rows: 2, tooltip: result.snippet }}
                    >
                      {result.snippet}
                    </Paragraph>
                  )}

                  {/* 底部信息行 */}
                  <Space
                    style={{
                      width: '100%',
                      justifyContent: 'space-between',
                      fontSize: 10,
                    }}
                  >
                    <Space size={4}>
                      {locationStr && (
                        <Text type="secondary" style={{ fontSize: 10 }}>
                          {locationStr}
                        </Text>
                      )}
                      <Text
                        code
                        style={{ fontSize: 9 }}
                      >
                        {result.content_hash.substring(0, 12)}…
                      </Text>
                    </Space>
                    {result.source_uri && (
                      <Button
                        size="small"
                        type="link"
                        icon={<LinkOutlined />}
                        href={result.source_uri}
                        target="_blank"
                        onClick={(e) => e.stopPropagation()}
                        style={{ fontSize: 10, padding: 0 }}
                      >
                        来源
                      </Button>
                    )}
                  </Space>
                </div>
              </List.Item>
            );
          }}
        />
      )}
    </Card>
  );
}
