/**
 * WorkspaceTimeline — paged timeline of research turns.
 *
 * Renders turn cards in descending order (newest first).
 * question_draft cards have a "开始分析" button.
 * run_failed cards have a "重试" button.
 */

import { useState, useEffect, useRef } from "react";
import { Button, Spin, Empty, Tag, Typography, Popconfirm, message } from "antd";
import { DeleteOutlined } from "@ant-design/icons";
import { useResearchTimeline } from "./useResearchTimeline";
import { startPlanning, submitRun } from "@/api/researchTimeline";
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
      // question_draft → generate plan; plan_confirmed / run_failed → submit run.
      const item = items.find((it) => it.turn_id === turnId);
      if (item && item.status === "question_draft") {
        await startPlanning(workspaceId, turnId);
        message.success("已开始生成分析计划");
      } else {
        await submitRun(workspaceId, turnId);
        message.success("分析已启动");
      }
      refresh();
      onTurnChanged?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "启动失败";
      message.error(msg);
    } finally {
      setAnalyzing(null);
    }
  };

  /** Map a turn status to the inline timeline action button (if any). */
  const actionForStatus = (status: string): { label: string; disabled: boolean } | null => {
    switch (status) {
      case "question_draft":
        return { label: "生成计划", disabled: false };
      case "planning":
        return { label: "计划生成中", disabled: true };
      case "plan_review":
        return { label: "待确认计划", disabled: true };
      case "plan_confirmed":
        return { label: "执行分析", disabled: false };
      case "run_failed":
        return { label: "重试", disabled: false };
      case "queued":
      case "running":
        return { label: STATUS_LABELS[status] || status, disabled: true };
      default:
        return null;
    }
  };

  const [deleting, setDeleting] = useState<string | null>(null);
  const handleDelete = async (e: React.MouseEvent, turnId: string) => {
    e.stopPropagation();
    setDeleting(turnId);
    try {
      await http.delete(`/research/workspaces/${workspaceId}/turns/${turnId}`);
      message.success("已删除");
      refresh();
      onTurnChanged?.();
    } catch {
      message.error("删除失败");
    } finally {
      setDeleting(null);
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
          style={{
            padding: "12px 16px",
            marginBottom: 8,
            border: "1px solid #f0f0f0",
            borderRadius: 6,
            position: "relative",
          }}
          data-testid="research-turn-card"
        >
          <div
            style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", cursor: onTurnClick ? "pointer" : "default" }}
            onClick={() => onTurnClick?.(item.turn_id)}
          >
            <Text strong style={{ flex: 1, minWidth: 0 }}>
              {"#"}{item.turn_number} {item.question_text}
            </Text>
            <Tag color={STATUS_COLORS[item.status] || "default"}>
              {STATUS_LABELS[item.status] || item.status}
            </Tag>
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: "#999", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span>{"快照 v"}{item.snapshot_number}</span>
              {item.selected_conclusion_count > 0 && (
                <span>{" · 引用 "}{item.selected_conclusion_count}{" 条结论"}</span>
              )}
              {item.has_result && <span>{" · 有结果"}</span>}
              {item.has_candidates && <span>{" · 有候选"}</span>}
              {(() => {
                const action = actionForStatus(item.status);
                if (!action) return null;
                return (
                  <Button
                    type="link"
                    size="small"
                    loading={analyzing === item.turn_id}
                    disabled={action.disabled}
                    onClick={(e) => handleAnalyze(e, item.turn_id)}
                    style={{ padding: 0, fontSize: 12, height: 16, lineHeight: "16px" }}
                  >
                    {action.label}
                  </Button>
                );
              })()}
            </div>
            <div>
              <Popconfirm
                title="确认删除此轮次？"
                description="删除后不可恢复，关联的分析结果也会一并删除。"
                onConfirm={() => handleDelete({ stopPropagation: () => {} } as React.MouseEvent, item.turn_id)}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button
                  type="text"
                  size="small"
                  icon={<DeleteOutlined />}
                  loading={deleting === item.turn_id}
                  onClick={(e) => e.stopPropagation()}
                  danger
                  style={{ padding: '0 4px' }}
                />
              </Popconfirm>
            </div>
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
