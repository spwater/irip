/**
 * ConclusionBarPanel — 右栏 Tab 容器（结论栏 / 结论库 / 发布成果）。
 *
 * Ant Design Tabs：
 * - Tab1 "结论栏"：ConclusionBar（新）
 * - Tab2 "结论库"：ConclusionLibrary + 发布结果按钮（替代原 SynthesisComposer）
 * - Tab3 "发布成果"：已发布成果列表 + 详情 Modal
 *
 * finalize 成功后自动切换到结论库 Tab，并刷新 conclusions 列表。
 * 发布成功后自动切换到发布成果 Tab，并刷新 results 列表。
 */
import { useState } from 'react';
import { Button, Card, Empty, Spin, Tabs, Tag, Typography, message } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ConclusionRef } from '@/api/researchTimeline';
import { ConclusionBar } from './ConclusionBar';
import { ConclusionLibrary } from './ConclusionLibrary';
import { ResultDetailModal } from './ResultDetailModal';
import {
  apiListResults,
  apiPublishConclusion,
  genResultIdempotencyKey,
} from '@/api/researchResults';

const { Text } = Typography;

type TabKey = 'bar' | 'library' | 'results';

interface Props {
  workspaceId: string;
  /** 结论库列表 */
  conclusions: ConclusionRef[];
  /** 已选历史结论 revision id 集合 */
  selectedRevisionIds: Set<string>;
  /** 切换历史结论勾选 */
  onToggleConclusion: (revisionId: string) => void;
  maxSelection?: number;
  /** 结论库变更回调（删除 / finalize 后刷新） */
  onConclusionsChanged?: () => void;
  /** 是否有数据快照（控制提示文案） */
  hasSnapshot: boolean;
}

/** 把 UTC 时间字符串转成本地时间显示 */
function fmtTime(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString('zh-CN', { hour12: false });
}

export function ConclusionBarPanel({
  workspaceId,
  conclusions,
  selectedRevisionIds,
  onToggleConclusion,
  maxSelection = 20,
  onConclusionsChanged,
  hasSnapshot,
}: Props): JSX.Element {
  const [activeTab, setActiveTab] = useState<TabKey>('bar');
  const [detailResultId, setDetailResultId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const queryClient = useQueryClient();

  // 发布成果列表
  const { data: results, isLoading: resultsLoading } = useQuery({
    queryKey: ['research-results', workspaceId],
    queryFn: () => apiListResults(workspaceId),
    enabled: activeTab === 'results',
  });

  // 发布 mutation
  const publishMutation = useMutation({
    mutationFn: async () => {
      // 找到所有已选结论的 conclusion_id
      const selected = conclusions.filter(
        (c) => c.current_revision_id && selectedRevisionIds.has(c.current_revision_id),
      );
      if (selected.length === 0) return [];
      const idempotencyKey = genResultIdempotencyKey();
      return Promise.all(
        selected.map((c) =>
          apiPublishConclusion(workspaceId, c.conclusion_id, { idempotency_key: idempotencyKey }),
        ),
      );
    },
    onSuccess: () => {
      message.success('已发布');
      // 刷新成果列表
      void queryClient.invalidateQueries({ queryKey: ['research-results', workspaceId] });
      // 切换到发布成果 Tab
      setActiveTab('results');
    },
    onError: (err: unknown) => {
      message.error(err instanceof Error ? err.message : String(err));
    },
  });

  const handleFinalized = (): void => {
    setActiveTab('library');
    onConclusionsChanged?.();
  };

  const handleOpenDetail = (resultId: string): void => {
    setDetailResultId(resultId);
    setDetailOpen(true);
  };

  // 已选结论数量
  const selectedCount = selectedRevisionIds.size;

  const items = [
    {
      key: 'bar' as const,
      label: '结论栏',
      children: (
        <ConclusionBar workspaceId={workspaceId} onFinalized={handleFinalized} />
      ),
    },
    {
      key: 'library' as const,
      label: `结论库 (${conclusions.length})`,
      children: (
        <>
          <ConclusionLibrary
            conclusions={conclusions}
            selectedRevisionIds={selectedRevisionIds}
            onToggle={onToggleConclusion}
            maxSelection={maxSelection}
            workspaceId={workspaceId}
            onDeleted={onConclusionsChanged}
          />
          {/* 发布结果按钮（替代原 SynthesisComposer） */}
          {selectedCount >= 1 && (
            <div style={{ marginTop: 12 }}>
              <Button
                type="primary"
                loading={publishMutation.isPending}
                disabled={publishMutation.isPending}
                onClick={() => publishMutation.mutate()}
                block
              >
                发布结果 ({selectedCount} 条)
              </Button>
            </div>
          )}
          {/* 提示文案（需求 1） */}
          {hasSnapshot && (
            <div
              style={{
                marginTop: 16,
                padding: 12,
                background: 'var(--ocean-surface-structural)',
                borderRadius: 6,
              }}
            >
              <Text type="secondary" style={{ fontSize: 13 }}>
                {'勾选结论后，在提问区会自动作为下一轮分析的背景上下文。'}
              </Text>
            </div>
          )}
        </>
      ),
    },
    {
      key: 'results' as const,
      label: `发布成果 (${results?.length ?? 0})`,
      children: resultsLoading ? (
        <div style={{ textAlign: 'center', padding: 32 }}>
          <Spin tip="加载中…" />
        </div>
      ) : !results || results.length === 0 ? (
        <Empty
          description="暂无已发布成果"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <div data-testid="result-list">
          {results.map((r) => (
            <Card
              key={r.id}
              size="small"
              hoverable
              onClick={() => handleOpenDetail(r.id)}
              style={{ marginBottom: 8, cursor: 'pointer' }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <Text strong>{r.name}</Text>
                  <div style={{ marginTop: 4, fontSize: 12 }}>
                    <Tag color={r.status === 'published' ? 'green' : 'default'}>
                      {r.status}
                    </Tag>
                    <Text type="secondary" style={{ marginLeft: 8 }}>
                      v{r.current_version}
                    </Text>
                    <Text type="secondary" style={{ marginLeft: 8 }}>
                      {fmtTime(r.created_at)}
                    </Text>
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      ),
    },
  ];

  return (
    <>
      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as TabKey)}
        items={items}
        size="small"
      />
      <ResultDetailModal
        workspaceId={workspaceId}
        resultId={detailResultId}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
      />
    </>
  );
}

export default ConclusionBarPanel;
