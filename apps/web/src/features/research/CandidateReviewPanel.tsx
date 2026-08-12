/**
 * CandidateReviewPanel — review AI-extracted candidates.
 *
 * States: waiting (queued/running), failed, succeeded.
 * Succeeded 0 candidates: "未提取到足够支持的结论" (not an error).
 * Succeeded >0: multi-select, per-item edit, reject, batch save.
 */

import { Button, Spin, Alert, Checkbox, Input, Tag, Empty, Typography } from "antd";
import { useState } from "react";
import type { ConclusionCandidate } from "../../api/researchTimeline";

const { Text } = Typography;
const { TextArea } = Input;

interface Props {
  extractionStatus: string | null;
  candidates: ConclusionCandidate[];
  onRetry?: () => void;
  onSave?: (selections: { candidate_id: string; edited_statement?: string }[]) => void;
  onAddManual?: () => void;
}

export function CandidateReviewPanel({
  extractionStatus,
  candidates,
  onRetry,
  onSave,
  onAddManual,
}: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  // Waiting for extraction
  if (extractionStatus === "queued" || extractionStatus === "running") {
    return (
      <div style={{ textAlign: "center", padding: "2rem" }} data-testid="candidate-waiting">
        <Spin tip="AI 正在提取候选结论... (关闭页面不影响)" />
      </div>
    );
  }

  // Extraction failed
  if (extractionStatus === "failed" || extractionStatus === "task_lost") {
    return (
      <div>
        <Alert
          type="warning"
          message="候选提取失败"
          description="可以重试提取或手动新增结论"
          action={onRetry && <Button size="small" onClick={onRetry}>重试</Button>}
        />
        {onAddManual && (
          <Button size="small" style={{ marginTop: 8 }} onClick={onAddManual}>
            手动新增结论
          </Button>
        )}
      </div>
    );
  }

  // Succeeded but 0 candidates — this is NOT an error
  if (extractionStatus === "succeeded" && candidates.length === 0) {
    return (
      <div>
        <Empty
          description="分析结果不足以支持任何候选结论"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
        {onAddManual && (
          <Button size="small" onClick={onAddManual}>
            手动新增结论
          </Button>
        )}
      </div>
    );
  }

  // Succeeded with candidates
  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSave = () => {
    const selections = candidates
      .filter((c) => selected.has(c.candidate_id))
      .map((c) => ({
        candidate_id: c.candidate_id,
        edited_statement: editing === c.candidate_id ? editText : undefined,
      }));
    onSave?.(selections);
    setSelected(new Set());
    setEditing(null);
  };

  return (
    <div data-testid="candidate-review">
      <div style={{ marginBottom: 8 }}>
        <Text type="secondary">
          候选结论 ({candidates.length} 条)
        </Text>
      </div>

      {candidates.map((c) => (
        <div
          key={c.candidate_id}
          style={{
            padding: "8px 12px",
            marginBottom: 6,
            border: "1px solid #f0f0f0",
            borderRadius: 4,
          }}
        >
          <div style={{ display: "flex", alignItems: "flex-start" }}>
            <Checkbox
              checked={selected.has(c.candidate_id)}
              onChange={() => toggle(c.candidate_id)}
              disabled={c.status === "saved" || c.status === "rejected"}
              style={{ marginRight: 8, marginTop: 2 }}
            />
            <div style={{ flex: 1 }}>
              {editing === c.candidate_id ? (
                <TextArea
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  autoSize={{ minRows: 2 }}
                />
              ) : (
                <Text>{c.statement}</Text>
              )}
              <div style={{ marginTop: 4, fontSize: 12, color: "#999" }}>
                {c.scope && <span>范围: {c.scope}</span>}
                {c.confidence_level && (
                  <Tag style={{ marginLeft: 4 }}>{c.confidence_level}</Tag>
                )}
                {c.status === "saved" && (
                  <Tag color="green">已保存</Tag>
                )}
                {c.status === "rejected" && (
                  <Tag color="red">已拒绝</Tag>
                )}
              </div>
              {c.status === "pending" && (
                <div style={{ marginTop: 4 }}>
                  <Button
                    size="small"
                    type="link"
                    onClick={() => {
                      setEditing(c.candidate_id);
                      setEditText(c.statement);
                    }}
                  >
                    编辑
                  </Button>
                </div>
              )}
            </div>
          </div>
        </div>
      ))}

      {selected.size > 0 && (
        <div style={{ marginTop: 8 }}>
          <Button type="primary" size="small" onClick={handleSave}>
            保存选中 ({selected.size})
          </Button>
        </div>
      )}

      {onAddManual && (
        <Button size="small" style={{ marginTop: 8 }} onClick={onAddManual}>
          手动新增结论
        </Button>
      )}
    </div>
  );
}
