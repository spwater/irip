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
import { Card, Empty, Spin, Tabs, Tag, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ConclusionBar } from './ConclusionBar';
import { ResultDetailModal } from './ResultDetailModal';
import { apiListResults } from '@/api/researchResults';

const { Text } = Typography;

type TabKey = 'bar' | 'library';

interface Props {
  workspaceId: string;
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
}: Props): JSX.Element {
  const [activeTab, setActiveTab] = useState<TabKey>('bar');
  const [detailResultId, setDetailResultId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const queryClient = useQueryClient();

  // 发布成果列表
  const { data: results, isLoading: resultsLoading } = useQuery({
    queryKey: ['research-results', workspaceId],
    queryFn: () => apiListResults(workspaceId),
  });

  const handleFinalized = (): void => {
    setActiveTab('library');
    void queryClient.invalidateQueries({ queryKey: ['research-results', workspaceId] });
  };

  const handleOpenDetail = (resultId: string): void => {
    setDetailResultId(resultId);
    setDetailOpen(true);
  };


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
      label: `结论库 (${results?.length ?? 0})`,
      children: resultsLoading ? (
        <div style={{ textAlign: 'center', padding: 32 }}>
          <Spin tip="加载中…" />
        </div>
      ) : !results || results.length === 0 ? (
        <Empty
          description="暂无结论，请在结论栏中推送并生成最终结论"
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
