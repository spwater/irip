/**
 * TurnDetailPanel — shows analysis results for a completed/in-progress turn.
 *
 * Clicking a timeline card opens this panel to show results, candidates,
 * and saved conclusions. No "start analysis" button — analysis auto-starts
 * when the turn is created.
 */

import { useState, useCallback, useEffect } from 'react';
import { Tag, Typography, Spin, message, Empty, Button, Collapse, Table } from 'antd';
import { SaveOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChartRefBlock } from '@/features/assistant/ChartRefBlock';
import { ChartBlock } from '@/features/assistant/message-thread/components/ChartBlock';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { http } from '@/api/client';
import type { TurnDetail } from '@/api/researchTimeline';

const { Text, Paragraph } = Typography;

interface Props {
  workspaceId: string;
  turnId: string;
  onClose: () => void;
  onConclusionSaved?: () => void;
}

const STATUS_LABELS: Record<string, string> = {
  question_draft: '待分析',
  queued: '排队中',
  running: '分析中',
  planning: '分析中',
  succeeded: '已完成',
  partially_succeeded: '部分完成',
  run_failed: '分析失败',
  cancelled: '已取消',
  succeeded_without_saved_conclusion: '无结论',
  candidate_review: '候选审阅',
  concluded: '已结论',
};

export function TurnDetailPanel({ workspaceId, turnId, onClose, onConclusionSaved }: Props) {
  const [detail, setDetail] = useState<TurnDetail | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchDetail = useCallback(async () => {
    try {
      setLoading(true);
      const res = await http.get<TurnDetail>(
        `/research/workspaces/${workspaceId}/turns/${turnId}`,
      );
      setDetail(res.data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败';
      message.error(msg);
    } finally {
      setLoading(false);
    }
  }, [workspaceId, turnId]);

  useEffect(() => {
    fetchDetail();
  }, [fetchDetail]);

  // Poll when running
  useEffect(() => {
    if (!detail) return;
    const active = ['queued', 'running', 'planning'];
    if (!active.includes(detail.turn.status)) return;
    const interval = setInterval(fetchDetail, 3000);
    return () => clearInterval(interval);
  }, [detail?.turn.status, fetchDetail]);

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 24 }}>
        <Spin tip="加载中..." />
      </div>
    );
  }

  if (!detail) {
    return <Empty description="无法加载轮次详情" />;
  }

  const { turn, result, candidates, access_restricted } = detail;
  const statusLabel = STATUS_LABELS[turn.status] || turn.status;
  const isActive = ['queued', 'running', 'planning'].includes(turn.status);

  return (
    <div
      style={{
        marginTop: 12,
        padding: 16,
        border: '1px solid #d6e4ff',
        borderRadius: 8,
        background: '#faffff',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <Text strong>
          <ArrowLeftOutlined
            style={{ marginRight: 8, cursor: 'pointer' }}
            onClick={onClose}
          />
          {'轮次 #'}{turn.turn_number}
        </Text>
        <Tag color={
          turn.status === 'succeeded' ? 'green' :
          turn.status === 'run_failed' ? 'red' :
          isActive ? 'blue' : 'default'
        }>
          {statusLabel}
        </Tag>
      </div>

      {/* Question */}
      <div style={{ marginBottom: 12 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>{'研究问题'}</Text>
        <Paragraph style={{ marginTop: 4 }}>{turn.question_text}</Paragraph>
      </div>

      {/* Access restricted */}
      {access_restricted && (
        <div style={{ padding: 8, background: '#fffbe6', borderRadius: 4, marginBottom: 12 }}>
          <Text type="warning" style={{ fontSize: 12 }}>
            {'⚠ 此轮次引用了已不可访问的数据，部分内容可能受限'}
          </Text>
        </div>
      )}

      {/* Selected conclusions */}
      {detail.selected_conclusions.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {'引用的历史结论 ('}{detail.selected_conclusions.length}{')'}
          </Text>
          {detail.selected_conclusions.map((c) => (
            <div key={c.revision_id} style={{ padding: '4px 8px', background: '#f5f5f5', borderRadius: 4, marginBottom: 4 }}>
              <Text style={{ fontSize: 12 }}>{c.statement}</Text>
              {c.source_type === 'manual' && (
                <Tag style={{ marginLeft: 4 }} color="orange">{'人工'}</Tag>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Running spinner */}
      {isActive && (
        <div style={{ textAlign: 'center', padding: 24, background: '#f0f5ff', borderRadius: 6 }}>
          <Spin size="large" />
          <div style={{ marginTop: 12 }}>
            <Text type="secondary">{'AI 正在分析数据...'}</Text>
          </div>
        </div>
      )}

      {/* Failed with retry */}
      {turn.status === 'run_failed' && (
        <div style={{ textAlign: 'center', padding: 16 }}>
          <Text type="danger">{'分析失败'}</Text>
          <div style={{ marginTop: 8 }}>
            <Button
              size="small"
              onClick={async () => {
                await http.post(`/research/workspaces/${workspaceId}/turns/${turnId}/analyze`);
                fetchDetail();
              }}
            >
              {'重试'}
            </Button>
          </div>
        </div>
      )}

      {/* Result — render as Markdown */}
      {result && (
        <div style={{ marginTop: 12 }}>
          <Text strong style={{ display: 'block', marginBottom: 8 }}>{'分析结果'}</Text>
          <div className="research-markdown" style={{ fontSize: 14, lineHeight: 1.8 }}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                pre({ children }) {
                  return <>{children}</>;
                },
                code({ className, children }) {
                  const lang = className?.replace('language-', '') || '';
                  const codeStr = String(children || '').replace(/\n$/, '');
                  if (lang === 'chart-ref' || lang === 'chart') {
                    return <ChartRefBlock key={`chartref-${codeStr.slice(0, 20)}`} specStr={codeStr} systemContext={detail.fact_context} />;
                  }
                  if (lang === 'echarts') {
                    let echartsStr = codeStr
                      .replace(/"formatter"\s*:\s*function\s*\([^)]*\)\s*\{[\s\S]*?\}\s*(?=,\s*")/g, '"formatter": "{b}: {c}"')
                      .replace(/"formatter"\s*:\s*\([^)]*\)\s*=>\s*\{[\s\S]*?\}\s*(?=,\s*")/g, '"formatter": "{b}: {c}"');
                    if (echartsStr.includes('function')) {
                      echartsStr = echartsStr.replace(/"formatter"\s*:\s*function[\s\S]*?\}\s*,/g, '"formatter": "{b}: {c}",');
                    }
                    return <ChartBlock key={`echarts-${codeStr.slice(0, 20)}`} optionStr={echartsStr} />;
                  }
                  if (lang === 'data' || lang === 'json') {
                    try {
                      const parsed = JSON.parse(codeStr);
                      return <StructuredDataBlock data={parsed} workspaceId={workspaceId} turnId={turnId} onSaved={onConclusionSaved} />;
                    } catch {
                      return (
                        <details style={{ margin: '8px 0' }}>
                          <summary style={{ cursor: 'pointer', fontSize: 12, color: '#8c8c8c' }}>
                            {'结构化数据（点击展开）'}
                          </summary>
                          <pre style={{ fontSize: 10, maxHeight: 300, overflow: 'auto', background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
                            <code>{children}</code>
                          </pre>
                        </details>
                      );
                    }
                  }
                  return <code className={className}>{children}</code>;
                },
              }}
            >
              {String((result.structured_output as Record<string, unknown>)?.analysis_markdown ?? result.summary ?? '')}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {/* Candidates */}
      {candidates.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Collapse
            size="small"
            items={[{
              key: 'candidates',
              label: <Text strong>{'候选结论 ('}{candidates.length}{')'}</Text>,
              children: candidates.map((c) => (
                <div key={c.candidate_id} style={{ padding: 8, border: '1px solid #f0f0f0', borderRadius: 4, marginBottom: 4 }}>
                  <Text>{c.statement}</Text>
                  {c.confidence_level && (
                    <Tag style={{ marginLeft: 4 }} color={
                      c.confidence_level === 'high' ? 'green' :
                      c.confidence_level === 'medium' ? 'gold' : 'default'
                    }>
                      {c.confidence_level}
                    </Tag>
                  )}
                </div>
              )),
            }]}
          />
        </div>
      )}

    </div>
  );
}

/** Render structured data (metadata/points/series) as collapsible tables with save-as-conclusion */
function StructuredDataBlock({ data, workspaceId, turnId, onSaved }: { data: Record<string, unknown>; workspaceId: string; turnId: string; onSaved?: () => void }) {
  const [saving, setSaving] = useState<string | null>(null);

  const handleSave = async (key: string, statement: string) => {
    setSaving(key);
    try {
      await http.post(`/research/workspaces/${workspaceId}/turns/${turnId}/save-conclusion`, {
        statement,
        block_type: 'structured',
      });
      message.success('已保存为结论');
      onSaved?.();
    } catch (err) {
      message.error('保存失败');
    } finally {
      setSaving(null);
    }
  };

  const metadata = data.metadata as Record<string, unknown> | undefined;
  const points = data.points as Array<Record<string, unknown>> | undefined;
  const series = data.series as Array<Record<string, unknown>> | undefined;

  // One save button for the entire structured data block (metadata + points + series as one conclusion)
  const fullText = JSON.stringify(data, null, 2);

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

  return (
    <div style={{ margin: '8px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
        <Button type="link" size="small" icon={<SaveOutlined />} loading={saving === 'block'} onClick={() => handleSave('block', fullText)} style={{ fontSize: 11, padding: 0 }}>
          {'存为结论'}
        </Button>
      </div>
      <Collapse size="small" items={items} />
    </div>
  );
}
