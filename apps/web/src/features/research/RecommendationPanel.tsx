/**
 * RecommendationPanel — displays 1-4 AI-recommended questions.
 *
 * States: queued, running, succeeded, failed, none.
 * Success: renders actual items, no placeholder for "missing" slots.
 * Failure: shows retry button + manual question entry.
 */

import { Button, Spin, Alert, Input, Typography } from "antd";
import { useEffect, useState, useCallback, useRef } from "react";
import {
  getActiveRecommendation,
  retryRecommendation,
} from "../../api/researchTimeline";
import type { RecommendationBatch } from "../../api/researchTimeline";

const { Text } = Typography;
const { TextArea } = Input;

interface Props {
  workspaceId: string;
  snapshotNumber: number | null;
  refreshKey: number;  // changed by parent when snapshot frozen or analysis completed
  onAdopt: (question: string, itemId: string | null) => void;
}

export function RecommendationPanel({ workspaceId, snapshotNumber, refreshKey, onAdopt }: Props) {
  const [batch, setBatch] = useState<RecommendationBatch | null>(null);
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState(false);
  const [manualQuestion, setManualQuestion] = useState("");
  const isFirstFetch = useRef(true);

  const fetchBatch = useCallback(async () => {
    try {
      if (isFirstFetch.current) {
        setLoading(true);
      }
      const data = await getActiveRecommendation(workspaceId);
      setBatch(data);
    } catch {
      setBatch(null);
    } finally {
      isFirstFetch.current = false;
      setLoading(false);
    }
  }, [workspaceId]);

  // Fetch on mount and when refreshKey changes (snapshot frozen or analysis completed)
  useEffect(() => {
    fetchBatch();
  }, [fetchBatch, refreshKey]);

  // Poll only while batch is generating — stop once succeeded/failed
  useEffect(() => {
    if (!batch || (batch.status !== "queued" && batch.status !== "running" && batch.status !== "none")) {
      return;
    }
    const interval = setInterval(() => {
      fetchBatch();
    }, 5000);
    return () => clearInterval(interval);
  }, [batch?.status, fetchBatch]);

  const handleRetry = async () => {
    if (!batch || !batch.batch_id) return;
    try {
      setRetrying(true);
      await retryRecommendation(workspaceId, batch.batch_id);
      await fetchBatch();
    } finally {
      setRetrying(false);
    }
  };

  if (!snapshotNumber) {
    return (
      <div>
        <Text type="secondary">请先冻结快照，AI 将自动推荐研究问题。</Text>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: "1rem" }}>
        <Spin tip="AI 正在分析数据..." />
      </div>
    );
  }

  if (batch && batch.status === "failed") {
    return (
      <div data-testid="recommendation-panel">
        <Alert
          type="warning"
          message="推荐生成失败"
          description="可能是 AI 配置不正确或 API Key 过期，请检查 AI 配置后重试。"
          action={
            <Button size="small" loading={retrying} onClick={handleRetry}>
              重试
            </Button>
          }
        />
        <Text type="secondary" style={{ marginTop: 12, display: "block" }}>
          也可以直接手动提问：
        </Text>
        <TextArea
          value={manualQuestion}
          onChange={(e) => setManualQuestion(e.target.value)}
          placeholder="输入研究问题..."
          autoSize={{ minRows: 2 }}
          style={{ marginTop: 8 }}
        />
        <Button
          type="primary"
          size="small"
          style={{ marginTop: 8 }}
          disabled={!manualQuestion.trim()}
          onClick={() => onAdopt(manualQuestion, null)}
        >
          提交问题
        </Button>
      </div>
    );
  }

  if (batch && (batch.status === "queued" || batch.status === "running")) {
    return (
      <div style={{ textAlign: "center", padding: "1rem" }}>
        <Spin tip="AI 正在生成推荐问题..." />
      </div>
    );
  }

  if (batch && batch.items.length > 0) {
    return (
      <div data-testid="recommendation-panel">
        <Text type="secondary" style={{ marginBottom: 8, display: "block", fontSize: 12 }}>
          {"AI 推荐研究问题"}
        </Text>
        {batch.items.map((item, i) => (
          <div
            key={item.id}
            style={{
              padding: "8px 10px",
              marginBottom: 6,
              borderLeft: "3px solid #1890ff",
              borderRadius: 4,
              background: "#f6faff",
              cursor: "pointer",
            }}
            onClick={() => onAdopt(item.question, item.id)}
          >
            <Text style={{ fontSize: 13, lineHeight: 1.5 }}>
              <span style={{ color: "#1890ff", fontWeight: 600, marginRight: 6 }}>
                {"#"}{i + 1}
              </span>
              {item.question}
            </Text>
          </div>
        ))}
      </div>
    );
  }

  // No batch yet (snapshot just frozen, batch being created) — show generating
  if (batch && batch.status === "none" && snapshotNumber) {
    return (
      <div style={{ textAlign: "center", padding: "1rem" }}>
        <Spin size="small" />
        <div style={{ marginTop: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>{"正在生成推荐问题..."}</Text>
        </div>
      </div>
    );
  }

  // No recommendations yet — show manual entry
  return (
    <div>
      <Text type="secondary">没有推荐问题，可以直接提问：</Text>
      <TextArea
        value={manualQuestion}
        onChange={(e) => setManualQuestion(e.target.value)}
        placeholder="输入研究问题..."
        autoSize={{ minRows: 2 }}
        style={{ marginTop: 8 }}
      />
      <Button
        type="primary"
        size="small"
        style={{ marginTop: 8 }}
        disabled={!manualQuestion.trim()}
        onClick={() => onAdopt(manualQuestion, null)}
      >
        提交问题
      </Button>
    </div>
  );
}
