/**
 * ConclusionLibrary — right-column conclusion list with selection state.
 *
 * Shows current revisions, source badges, evidence status, and
 * "newer revision" / "snapshot outdated" warnings.
 * Selection is a local draft until "用于下一轮" is committed.
 */

import { useState } from "react";
import { Checkbox, Tag, Empty, Typography, Badge, Button, Popconfirm, message, Collapse, Table } from "antd";
import { DeleteOutlined } from "@ant-design/icons";
import { http } from "@/api/client";
import type { ConclusionRef } from "../../api/researchTimeline";

const { Text } = Typography;

/** Try to parse statement as JSON (structured data). Return null if not JSON. */
function tryParseStructured(statement: string): Record<string, unknown> | null {
  const trimmed = statement.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

/** Render structured data (metadata/points/series) as collapsible tables — same as TurnDetailPanel. */
export function StructuredConclusionDisplay({ data }: { data: Record<string, unknown> }) {
  const metadata = data.metadata as Record<string, unknown> | undefined;
  const points = data.points as Array<Record<string, unknown>> | undefined;
  const series = data.series as Array<Record<string, unknown>> | undefined;

  const items: Array<{ key: string; label: React.ReactNode; children: React.ReactNode }> = [];

  if (metadata && Object.keys(metadata).length > 0) {
    items.push({
      key: 'metadata',
      label: <Text style={{ fontSize: 12 }}>{'元数据'}</Text>,
      children: (
        <Table
          size="small"
          pagination={false}
          dataSource={Object.entries(metadata).map(([k, v], i) => ({ key: i, field: k, value: String(v) }))}
          columns={[
            { title: '字段', dataIndex: 'field', key: 'field', width: 120 },
            { title: '值', dataIndex: 'value', key: 'value' },
          ]}
        />
      ),
    });
  }

  if (points && points.length > 0) {
    items.push({
      key: 'points',
      label: <Text style={{ fontSize: 12 }}>{'数据点 ('}{points.length}{')'}</Text>,
      children: (
        <Table
          size="small"
          pagination={false}
          dataSource={points.map((p, i) => ({ key: i, ...p }))}
          columns={[
            { title: '指标', dataIndex: 'name', key: 'name', width: 200 },
            { title: '值', dataIndex: 'value', key: 'value', width: 100 },
            { title: '单位', dataIndex: 'unit', key: 'unit', width: 80 },
            { title: '描述', dataIndex: '描述', key: 'desc' },
          ]}
        />
      ),
    });
  }

  if (series && series.length > 0) {
    series.forEach((s, idx) => {
      const name = (s.name as string) || `数据组 ${idx + 1}`;
      const columns = (s.columns as string[]) || [];
      const rows = (s.rows as unknown[][]) || [];
      const sKey = `series-${idx}`;
      items.push({
        key: sKey,
        label: <Text style={{ fontSize: 12 }}>{name}{' ('}{rows.length}{' 行)'}</Text>,
        children: (
          <Table
            size="small"
            pagination={rows.length > 20 ? { pageSize: 10, size: 'small' as const } : false}
            dataSource={rows.map((row, i) => {
              const rowObj: Record<string, unknown> = { key: i };
              columns.forEach((col, ci) => { rowObj[col] = row[ci]; });
              return rowObj;
            })}
            columns={columns.map((col, ci) => ({
              title: col,
              dataIndex: col,
              key: ci,
            }))}
            scroll={{ x: true }}
          />
        ),
      });
    });
  }

  if (items.length === 0) {
    return <Text type="secondary" style={{ fontSize: 12 }}>{'（空数据）'}</Text>;
  }

  return <Collapse size="small" items={items} />;
}

interface ConclusionItem extends ConclusionRef {
  source_turn_number?: number | null;
  snapshot_number?: number | null;
  newer_revision_available?: boolean;
  snapshot_outdated?: boolean;
}

interface Props {
  conclusions: ConclusionItem[];
  selectedRevisionIds: Set<string>;
  onToggle: (revisionId: string) => void;
  maxSelection?: number;
  workspaceId?: string;
  onDeleted?: () => void;
}

const SOURCE_BADGE: Record<string, { label: string; color: string }> = {
  ai_original: { label: "AI", color: "blue" },
  ai_edited: { label: "AI（修改）", color: "cyan" },
  manual: { label: "人工", color: "orange" },
  assembled: { label: "组装", color: "purple" },
};

export function ConclusionLibrary({
  conclusions,
  selectedRevisionIds,
  onToggle,
  maxSelection = 20,
  workspaceId,
  onDeleted,
}: Props) {
  const [deleting, setDeleting] = useState<string | null>(null);

  const handleDelete = async (conclusionId: string) => {
    setDeleting(conclusionId);
    try {
      await http.delete(`/research/workspaces/${workspaceId}/conclusions/${conclusionId}`);
      message.success('已删除');
      onDeleted?.();
    } catch {
      message.error('删除失败');
    } finally {
      setDeleting(null);
    }
  };

  if (conclusions.length === 0) {
    return (
      <Empty
        description="暂无已保存结论"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    );
  }

  const atMax = selectedRevisionIds.size >= maxSelection;

  return (
    <div data-testid="conclusion-library">
      <div style={{ marginBottom: 8 }}>
        <Text type="secondary">
          结论库 ({conclusions.length} 条)
          {selectedRevisionIds.size > 0 && (
            <Text type="warning"> · 已选 {selectedRevisionIds.size}/{maxSelection}</Text>
          )}
        </Text>
      </div>

      {conclusions.map((conclusion) => {
        const isSelected = selectedRevisionIds.has(conclusion.current_revision_id ?? '');
        const disabled = !isSelected && atMax;
        const badge = SOURCE_BADGE[conclusion.source_type] || {
          label: conclusion.source_type,
          color: "default",
        };

        return (
          <div
            key={conclusion.conclusion_id}
            style={{
              padding: "8px 12px",
              marginBottom: 6,
              border: "1px solid #f0f0f0",
              borderRadius: 4,
              opacity: disabled ? 0.5 : 1,
            }}
          >
            <div style={{ display: "flex", alignItems: "flex-start" }}>
              <Checkbox
                checked={isSelected}
                onChange={() => onToggle(conclusion.current_revision_id ?? '')}
                disabled={disabled}
                style={{ marginRight: 8, marginTop: 2 }}
              />
              <div style={{ flex: 1 }}>
                {(() => {
                  const structured = tryParseStructured(conclusion.statement);
                  if (structured) {
                    return <StructuredConclusionDisplay data={structured} />;
                  }
                  return <Text>{conclusion.statement}</Text>;
                })()}
                <div style={{ marginTop: 4, fontSize: 12 }}>
                  <Tag color={badge.color}>{badge.label}</Tag>
                  {conclusion.evidence_status === "manual_unverified" && (
                    <Tag color="orange">{"未验证"}</Tag>
                  )}
                  {conclusion.source_turn_number != null && (
                    <span style={{ color: "#999" }}>
                      {"来源: 轮次 #"}{conclusion.source_turn_number}
                    </span>
                  )}
                  {conclusion.snapshot_number != null && (
                    <span style={{ color: "#999" }}>
                      {" · 快照 v"}{conclusion.snapshot_number}
                    </span>
                  )}
                  {conclusion.newer_revision_available && (
                    <Badge
                      count={"新版本"}
                      style={{ backgroundColor: "#faad14", marginLeft: 4 }}
                    />
                  )}
                  {conclusion.snapshot_outdated && (
                    <Text type="warning" style={{ fontSize: 12, marginLeft: 4 }}>
                      {"尚未基于最新数据复核"}
                    </Text>
                  )}
                </div>
              </div>
              {workspaceId && (
                <Popconfirm
                  title="确认删除此结论？"
                  onConfirm={() => handleDelete(conclusion.conclusion_id)}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                >
                  <Button
                    type="text"
                    size="small"
                    icon={<DeleteOutlined />}
                    loading={deleting === conclusion.conclusion_id}
                    style={{ color: '#999', flexShrink: 0 }}
                  />
                </Popconfirm>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
