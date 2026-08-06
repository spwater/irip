/**
 * CandidatePreviewPanel — 候选产物预览区（增强版）
 *
 * 三个分区：候选数据 / 候选图表 / 候选 Insight
 * 确认/接受/修改/拒绝操作后实时更新
 */
import { useState, useEffect, useCallback } from 'react';
import { Card, Spin, Empty, Typography, message } from 'antd';
import {
  apiGetCandidates,
  apiAcceptCandidate,
  apiRejectCandidate,
  type CandidateProduct,
} from '@/api/researchProducts';
import { CandidateDataCard } from './CandidateDataCard';
import { CandidateChartCard } from './CandidateChartCard';
import { CandidateInsightCard } from './CandidateInsightCard';
import { InsightModifyModal } from './InsightModifyModal';

const { Text } = Typography;

export type CandidatePreviewPanelProps = {
  workspaceId: string;
  runId: string;
  onProductsChanged?: () => void;
};

export function CandidatePreviewPanel({
  workspaceId,
  runId,
  onProductsChanged,
}: CandidatePreviewPanelProps): JSX.Element {
  const [candidates, setCandidates] = useState<CandidateProduct[]>([]);
  const [loading, setLoading] = useState(false);
  const [modifyCandidate, setModifyCandidate] = useState<CandidateProduct | null>(null);

  const fetchCandidates = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    try {
      const res = await apiGetCandidates(workspaceId, runId);
      setCandidates(res?.items ?? []);
    } catch {
      message.error('加载候选产物失败');
    } finally {
      setLoading(false);
    }
  }, [workspaceId, runId]);

  useEffect(() => {
    void fetchCandidates();
  }, [fetchCandidates]);

  const handleConfirmData = useCallback(() => {
    void fetchCandidates();
    onProductsChanged?.();
  }, [fetchCandidates, onProductsChanged]);

  const handleConfirmChart = useCallback(() => {
    void fetchCandidates();
    onProductsChanged?.();
  }, [fetchCandidates, onProductsChanged]);

  const handleAcceptInsight = useCallback(
    async (candidateId: string) => {
      try {
        await apiAcceptCandidate(workspaceId, runId, candidateId);
        message.success('已接受 Insight');
        void fetchCandidates();
        onProductsChanged?.();
      } catch {
        message.error('接受失败');
      }
    },
    [workspaceId, runId, fetchCandidates, onProductsChanged],
  );

  const handleModifyInsight = useCallback((candidate: CandidateProduct) => {
    setModifyCandidate(candidate);
  }, []);

  const handleRejectInsight = useCallback(
    async (candidateId: string, reason?: string) => {
      try {
        await apiRejectCandidate(workspaceId, runId, candidateId, reason);
        message.success('已拒绝 Insight');
        void fetchCandidates();
      } catch {
        message.error('拒绝失败');
      }
    },
    [workspaceId, runId, fetchCandidates],
  );

  const dataCandidates = candidates.filter((c) => c.candidate_type === 'derived_dataset');
  const chartCandidates = candidates.filter((c) => c.candidate_type === 'view');
  const insightCandidates = candidates.filter((c) => c.candidate_type === 'insight');

  if (loading) {
    return (
      <Card size="small" title="候选产物" style={{ marginBottom: 12 }}>
        <div style={{ textAlign: 'center', padding: 20 }}>
          <Spin />
        </div>
      </Card>
    );
  }

  if (candidates.length === 0) {
    return null;
  }

  return (
    <Card size="small" title={`候选产物 (${candidates.length})`} style={{ marginBottom: 12 }}>
      {/* 候选数据 */}
      {dataCandidates.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Text strong>📊 候选数据 ({dataCandidates.length})</Text>
          <div style={{ marginTop: 8 }}>
            {dataCandidates.map((c) => (
              <CandidateDataCard
                key={c.candidate_id}
                workspaceId={workspaceId}
                candidate={c}
                onConfirm={handleConfirmData}
              />
            ))}
          </div>
        </div>
      )}

      {/* 候选图表 */}
      {chartCandidates.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Text strong>📈 候选图表 ({chartCandidates.length})</Text>
          <div style={{ marginTop: 8 }}>
            {chartCandidates.map((c) => (
              <CandidateChartCard
                key={c.candidate_id}
                workspaceId={workspaceId}
                candidate={c}
                onConfirm={handleConfirmChart}
              />
            ))}
          </div>
        </div>
      )}

      {/* 候选 Insight */}
      {insightCandidates.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Text strong>💡 候选 Insight ({insightCandidates.length})</Text>
          <div style={{ marginTop: 8 }}>
            {insightCandidates.map((c) => (
              <CandidateInsightCard
                key={c.candidate_id}
                workspaceId={workspaceId}
                runId={runId}
                candidate={c}
                onAccept={handleAcceptInsight}
                onModify={handleModifyInsight}
                onReject={handleRejectInsight}
              />
            ))}
          </div>
        </div>
      )}

      {/* Insight 修改弹窗 */}
      {modifyCandidate && (
        <InsightModifyModal
          workspaceId={workspaceId}
          runId={runId}
          candidate={modifyCandidate}
          onClose={() => setModifyCandidate(null)}
          onSuccess={() => {
            setModifyCandidate(null);
            void fetchCandidates();
            onProductsChanged?.();
          }}
        />
      )}
    </Card>
  );
}
