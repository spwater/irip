/**
 * SynthesisComposer — select 2-20 conclusions and create a synthesis turn.
 *
 * Less than 2: disabled with explanation.
 * Over 20: extra selections disabled.
 * After creation: enters same timeline as analysis turns.
 */

import { Button, Typography, Alert } from "antd";
import { useState } from "react";
import { createSynthesisTurn } from "../../api/researchTimeline";

const { Text } = Typography;

interface Props {
  workspaceId: string;
  snapshotId: string;
  selectedRevisionIds: string[];
  onCreated?: (turnId: string) => void;
}

const MIN = 2;
const MAX = 20;

export function SynthesisComposer({
  workspaceId,
  snapshotId,
  selectedRevisionIds,
  onCreated,
}: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const count = selectedRevisionIds.length;

  const canSynthesize = count >= MIN && count <= MAX;

  const handleSynthesize = async () => {
    if (!canSynthesize) return;
    setLoading(true);
    setError(null);
    try {
      const ref = await createSynthesisTurn(workspaceId, {
        evidence_snapshot_id: snapshotId,
        selected_conclusion_revision_ids: selectedRevisionIds,
      });
      onCreated?.(ref.turn_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div data-testid="synthesis-composer">
      {error && (
        <Alert
          type="error"
          message="综合失败"
          description={error}
          style={{ marginBottom: 8 }}
          closable
          onClose={() => setError(null)}
        />
      )}

      <div style={{ marginBottom: 8 }}>
        <Text type="secondary">
          已选 {count} 条结论 (需 {MIN}-{MAX} 条)
        </Text>
      </div>

      {count < MIN && (
        <Text type="warning" style={{ fontSize: 12, display: "block", marginBottom: 8 }}>
          至少选择 {MIN} 条结论才能进行综合分析
        </Text>
      )}

      <Button
        type="primary"
        onClick={handleSynthesize}
        disabled={!canSynthesize || loading}
        loading={loading}
      >
        综合所选
      </Button>
    </div>
  );
}
