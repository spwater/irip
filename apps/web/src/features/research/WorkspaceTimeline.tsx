/**
 * WorkspaceTimeline — paged timeline of research turns.
 *
 * Renders turn cards in descending order (newest first).
 * question_draft cards have a "开始分析" button.
 * run_failed cards have a "重试" button.
 */

import { useState, useEffect, useRef } from "react";
import { Button, Spin, Empty, Tag, Typography, message } from "antd";
import { useResearchTimeline } from "./useResearchTimeline";
import { http } from "@/api/client";

const { Text } = Typography;

const STATUS_COLORS: Record<string, string> = {
  question_draft: "default",
  planning: "processing",
  plan_review: "processing",
  plan_confirmed: "warning",
  queued: "warning",
  running: "processing",
  succeeded: "success",
  conclusion_reviewed: "success",
  planning_failed: "error",
  run_failed: "error",
  cancelled: "default",
  succeeded_without_saved_conclusion: "default",
};

const STATUS_LABELS: Record<string, string> = {
  question_draft: "待分析",
  planning: "分析中",
  plan_review: "分析中",
  plan_confirmed: "等待执行",
  queued: "排队中",
  running: "分析中",
  succeeded: "已完成",
  conclusion_reviewed: "已结论",
  planning_failed: "失败",
  run_failed: "失败",
  cancelled: "已取消",
  succeeded_without_saved_conclusion: "无结论",
};

interface Props {
  workspaceId: string;
  onTurnClick?: (turnId: string) => void;
  onTurnChanged?: () => void;
  onTurnCompleted?: () => void;
}

export function WorkspaceTimeline({ workspaceId, onTurnClick, onTurnChanged, onTurnCompleted }: Props) {
  const { items, loading, error, hasMore, loadMore, activeRunStatus, refresh } =
    useResearchTimeline(workspaceId);
  const [analyzing, setAnalyzing] = useState<string | null>(null);
  const prevStatusesRef = useRef<Record<string, string>>({});

  // Detect when a turn transitions to succeeded → notify parent
  useEffect(() => {
    for (const item of items) {
      const prev = prevStatusesRef.current[item.turn_id];
      if (prev && prev !== "succeeded" && item.status === "succeeded") {
        onTurnCompleted?.();
      }
      prevStatusesRef.current[item.turn_id] = item.status;
    }
  }, [items, onTurnCompleted]);

  const handleAnalyze = async (e: React.MouseEvent, turnId: string) => {
    e.stopPropagation();
    setAnalyzing(turnId);
    try {
      await http.post(`/research/workspaces/${workspaceId}/turns/${turnId}/analyze`);
      message.success("分析已启动");
      refresh();
      onTurnChanged?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "启动失败";
      message.error(msg);
    } finally {
      setAnalyzing(null);
    }
  };

  if (loading && items.length === 0) {
    return (
      <div style={{ textAlign: "center", padding: "2rem" }}>
        <Spin />
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: "1rem" }}>
        <Text type="danger">{"加载失败: "}{error.message}</Text>
        <br />
        <Button onClick={loadMore} size="small" style={{ marginTop: 8 }}>
          {"重试"}
        </Button>
      </div>
    );
  }

  if (items.length === 0) {
    return <Empty description="暂无研究轮次" />;
  }

  return (
    <div>
      {activeRunStatus && (
        <div
          style={{ marginBottom: 12, padding: "8px 12px", background: "#f0f5ff", borderRadius: 4 }}
        >
          <Text type="warning">
            {"工作空间有活跃分析任务 ("}{activeRunStatus}{")，其他轮次可编辑但不能执行"}
          </Text>
        </div>
      )}

      {items.map((item) => (
        <div
          key={item.turn_id}
          onClick={() => onTurnClick?.(item.turn_id)}
          style={{
            padding: "12px 16px",
            marginBottom: 8,
            border: "1px solid #f0f0f0",
            borderRadius: 6,
            cursor: onTurnClick ? "pointer" : "default",
            position: "relative",
          }}
          data-testid="research-turn-card"
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <Text strong>
              {"#"}{item.turn_number} {item.question_text}
            </Text>
            <Tag color={STATUS_COLORS[item.status] || "default"}>
              {STATUS_LABELS[item.status] || item.status}
            </Tag>
          </div>
          <Button
            type="link"
            size="small"
            loading={analyzing === item.turn_id}
            onClick={(e) => handleAnalyze(e, item.turn_id)}
            style={{ position: "absolute", right: 8, top: 38, padding: 0, fontSize: 12, height: 16, lineHeight: "16px", transform: "translateX(-16px)" }}
          >
            {item.status === "question_draft" ? "开始分析" : "重新分析"}
          </Button>
          <div style={{ marginTop: 4, fontSize: 12, color: "#999" }}>
            <span>{"快照 v"}{item.snapshot_number}</span>
            {item.selected_conclusion_count > 0 && (
              <span>{" · 引用 "}{item.selected_conclusion_count}{" 条结论"}</span>
            )}
            {item.has_result && <span>{" · 有结果"}</span>}
            {item.has_candidates && <span>{" · 有候选"}</span>}
          </div>
        </div>
      ))}

      {hasMore && (
        <div style={{ textAlign: "center", padding: "1rem" }}>
          <Button onClick={loadMore} loading={loading}>
            {"加载更多"}
          </Button>
        </div>
      )}
    </div>
  );
}
