/**
 * KnowledgeReferenceList — 知识引用快照列表组件
 *
 * 展示 Insight 关联的 KnowledgeReference 列表。
 * 每条引用展示：文档标题 / 文档版本 / 检索时间 / 来源 Provider /
 *   引用段落文本 / 位置信息（Section/Page/Chunk）/ content_hash / 来源链接
 *
 * research:manage 权限用户可查看完整快照内容（snippet_text）。
 * 普通用户仅可见文档标题和来源链接（不展示完整段落文本）。
 *
 * 参照 PRD 4.3 节 UI 设计与 arch-research-lineage.md 2.3 节文件 28/29。
 */
import { useState, useEffect, useCallback } from 'react';
import {
  Card,
  Spin,
  Empty,
  Typography,
  Tag,
  Space,
  Button,
  Tooltip,
  Collapse,
  message,
} from 'antd';
import {
  BookOutlined,
  LinkOutlined,
  ClockCircleOutlined,
  ApiOutlined,
  CopyOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
} from '@ant-design/icons';
import type { KnowledgeReferenceDetail } from '@/api/researchLineage';
import { apiListKnowledgeReferencesByInsight } from '@/api/researchLineage';

const { Text, Paragraph } = Typography;

// ============================================================
// Props
// ============================================================

export type KnowledgeReferenceListProps = {
  /** Insight ID */
  insightId: string;
  /** 是否有 research:manage 权限（控制是否展示完整 snippet_text） */
  hasManagePermission?: boolean;
};

// ============================================================
// 组件
// ============================================================

/**
 * KnowledgeReferenceList — 知识引用快照列表
 */
export function KnowledgeReferenceList({
  insightId,
  hasManagePermission = false,
}: KnowledgeReferenceListProps): JSX.Element {
  const [references, setReferences] = useState<KnowledgeReferenceDetail[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedRefs, setExpandedRefs] = useState<Set<string>>(new Set());

  // ---- 加载知识引用列表 ----
  const fetchReferences = useCallback(async () => {
    setLoading(true);
    try {
      // hasManagePermission 时请求完整内容
      const refs = await apiListKnowledgeReferencesByInsight(
        insightId,
        hasManagePermission,
      );
      setReferences(refs);
    } catch (err) {
      console.error('加载知识引用失败', err);
      setReferences([]);
    } finally {
      setLoading(false);
    }
  }, [insightId, hasManagePermission]);

  useEffect(() => {
    void fetchReferences();
  }, [fetchReferences]);

  // ---- 切换展开/折叠完整快照 ----
  const toggleExpand = useCallback((refId: string) => {
    setExpandedRefs((prev) => {
      const next = new Set(prev);
      if (next.has(refId)) {
        next.delete(refId);
      } else {
        next.add(refId);
      }
      return next;
    });
  }, []);

  // ---- 复制哈希 ----
  const handleCopyHash = useCallback((hash: string) => {
    void navigator.clipboard.writeText(hash);
    message.success('已复制哈希');
  }, []);

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 24 }}>
        <Spin tip="加载知识引用..." />
      </div>
    );
  }

  if (references.length === 0) {
    return (
      <Empty
        description="暂无知识引用"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        style={{ padding: 24 }}
      />
    );
  }

  return (
    <Card
      size="small"
      title={
        <Space>
          <BookOutlined />
          <Text strong>知识库引用快照 ({references.length})</Text>
        </Space>
      }
      style={{ marginBottom: 12 }}
    >
      <Collapse
        ghost
        items={references.map((refDetail, idx) => {
          const ref = refDetail.ref;
          const isExpanded = expandedRefs.has(ref.reference_id);
          const showFullSnippet = hasManagePermission && isExpanded;

          // 位置信息
          const locationParts: string[] = [];
          if (refDetail.section) locationParts.push(`Section ${refDetail.section}`);
          if (refDetail.page) locationParts.push(`Page ${refDetail.page}`);
          if (refDetail.chunk_id) locationParts.push(`Chunk ${refDetail.chunk_id}`);
          const locationStr = locationParts.join(', ');

          return {
            key: ref.reference_id,
            label: (
              <Space direction="vertical" size={2} style={{ width: '100%' }}>
                <Space size={4}>
                  <Text strong style={{ fontSize: 13 }}>
                    {ref.title || ref.document_id}
                  </Text>
                  <Tag color="purple" style={{ fontSize: 10 }}>
                    v{ref.document_version}
                  </Tag>
                </Space>
                <Space size={8} style={{ fontSize: 11 }}>
                  <span style={{ color: 'var(--ocean-text-muted, #6f8d9c)' }}>
                    <ApiOutlined /> {ref.provider_name}
                  </span>
                  {ref.retrieval_time && (
                    <span style={{ color: 'var(--ocean-text-muted, #6f8d9c)' }}>
                      <ClockCircleOutlined />{' '}
                      {new Date(ref.retrieval_time).toLocaleString()}
                    </span>
                  )}
                </Space>
              </Space>
            ),
            children: (
              <div>
                {/* 引用段落文本 */}
                {refDetail.snippet_text ? (
                  <div style={{ marginBottom: 8 }}>
                    <Space
                      style={{
                        marginBottom: 4,
                        justifyContent: 'space-between',
                        width: '100%',
                      }}
                    >
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        引用段落:
                      </Text>
                      {hasManagePermission && (
                        <Button
                          size="small"
                          type="text"
                          icon={
                            showFullSnippet ? <EyeInvisibleOutlined /> : <EyeOutlined />
                          }
                          onClick={() => toggleExpand(ref.reference_id)}
                          style={{ fontSize: 11 }}
                        >
                          {showFullSnippet ? '折叠' : '展开完整'}
                        </Button>
                      )}
                    </Space>
                    <div
                      style={{
                        background:
                          'var(--ocean-surface-structural, rgba(142,191,208,0.46))',
                        border: '1px solid var(--ocean-border-subtle, rgba(24,102,133,0.16))',
                        borderRadius: 'var(--ocean-radius-sm, 4px)',
                        padding: '8px 12px',
                        fontSize: 12,
                        lineHeight: 1.6,
                        maxHeight: showFullSnippet ? 'none' : 80,
                        overflow: showFullSnippet ? 'visible' : 'hidden',
                        position: 'relative',
                      }}
                    >
                      <Paragraph
                        style={{ margin: 0, fontSize: 12 }}
                        ellipsis={showFullSnippet ? false : { rows: 3 }}
                      >
                        {refDetail.snippet_text}
                      </Paragraph>
                      {!showFullSnippet && !hasManagePermission && (
                        <div
                          style={{
                            position: 'absolute',
                            bottom: 0,
                            left: 0,
                            right: 0,
                            height: 24,
                            background:
                              'linear-gradient(transparent, var(--ocean-surface-structural, rgba(142,191,208,0.46)))',
                            pointerEvents: 'none',
                          }}
                        />
                      )}
                    </div>
                    {!hasManagePermission && (
                      <Text
                        type="secondary"
                        style={{ fontSize: 10, fontStyle: 'italic' }}
                      >
                        需 research:manage 权限查看完整段落文本
                      </Text>
                    )}
                  </div>
                ) : (
                  <div style={{ marginBottom: 8 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      段落文本不可用（需 research:manage 权限）
                    </Text>
                  </div>
                )}

                {/* 位置信息 */}
                {locationStr && (
                  <div style={{ marginBottom: 4 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      位置:
                    </Text>
                    <Text style={{ fontSize: 12, marginLeft: 4 }}>{locationStr}</Text>
                  </div>
                )}

                {/* 研究问题上下文 */}
                {refDetail.research_question_context && (
                  <div style={{ marginBottom: 4 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      检索上下文:
                    </Text>
                    <Text
                      style={{ fontSize: 11, marginLeft: 4, color: 'var(--ocean-text-muted, #6f8d9c)' }}
                    >
                      {refDetail.research_question_context}
                    </Text>
                  </div>
                )}

                {/* content_hash + 来源链接 */}
                <Space
                  style={{
                    justifyContent: 'space-between',
                    width: '100%',
                    marginTop: 4,
                  }}
                >
                  <Space size={4}>
                    <Text type="secondary" style={{ fontSize: 10 }}>
                      hash:
                    </Text>
                    <Text code style={{ fontSize: 10 }}>
                      {ref.content_hash.substring(0, 16)}…
                    </Text>
                    <Tooltip title="复制哈希">
                      <Button
                        size="small"
                        type="text"
                        icon={<CopyOutlined />}
                        onClick={() => handleCopyHash(ref.content_hash)}
                        style={{ fontSize: 10 }}
                      />
                    </Tooltip>
                  </Space>
                  {ref.source_uri && (
                    <Button
                      size="small"
                      type="link"
                      icon={<LinkOutlined />}
                      href={ref.source_uri}
                      target="_blank"
                      style={{ fontSize: 11, padding: 0 }}
                    >
                      查看来源文档
                    </Button>
                  )}
                </Space>
              </div>
            ),
            extra: (
              <Tag color="purple" style={{ fontSize: 10 }}>
                #{idx + 1}
              </Tag>
            ),
          };
        })}
      />
    </Card>
  );
}
