/**
 * TurnDetailPanel — shows analysis results for a completed/in-progress turn.
 *
 * Clicking a timeline card opens this panel to show results, candidates,
 * and saved conclusions. No "start analysis" button — analysis auto-starts
 * when the turn is created.
 */

import { useState, useCallback, useEffect, useRef, type ReactNode, type ReactElement } from 'react';
import { Tag, Typography, Spin, message, Empty, Button, Collapse, Table } from 'antd';
import { SaveOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChartRefBlock } from '@/features/assistant/ChartRefBlock';
import { ChartBlock } from '@/features/assistant/message-thread/components/ChartBlock';
import { http } from '@/api/client';
import type { TurnDetail } from '@/api/researchTimeline';
import { ReportBlockWrapper, detectBlockType } from './ReportBlockWrapper';
import { buildTableSnapshot, type BlockType } from './blockUtils';

const { Text, Paragraph } = Typography;

interface Props {
  workspaceId: string;
  turnId: string;
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

export function TurnDetailPanel({ workspaceId, turnId, onConclusionSaved }: Props) {
  const [detail, setDetail] = useState<TurnDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const blockIndexRef = useRef(0);

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

  // Reset block index each render; blocks re-number in document order
  blockIndexRef.current = 0;
  const turnInfo = {
    workspaceId,
    turnId,
    turnNumber: turn.turn_number,
    snapshotNumber: null,
    questionText: turn.question_text,
  };

  /** Render the inner block content for a recognised code-block language. */
  const renderInnerBlock = (lang: string, codeStr: string): ReactNode => {
    if (lang === 'chart-ref' || lang === 'chart') {
      return (
        <ChartRefBlock specStr={codeStr} sampleData={detail.fact_samples} />
      );
    }
    if (lang === 'echarts') {
      let echartsStr = codeStr
        .replace(/"formatter"\s*:\s*function\s*\([^)]*\)\s*\{[\s\S]*?\}\s*(?=,\s*")/g, '"formatter": "{b}: {c}"')
        .replace(/"formatter"\s*:\s*\([^)]*\)\s*=>\s*\{[\s\S]*?\}\s*(?=,\s*")/g, '"formatter": "{b}: {c}"');
      if (echartsStr.includes('function')) {
        echartsStr = echartsStr.replace(/"formatter"\s*:\s*function[\s\S]*?\}\s*,/g, '"formatter": "{b}: {c}",');
      }
      return <ChartBlock optionStr={echartsStr} />;
    }
    if (lang === 'describe_series' || lang === 'describe-series' || lang === 'describeSeries') {
      try {
        const parsed = JSON.parse(codeStr);
        const rawData = Array.isArray(parsed) ? parsed[0] : parsed;
        if (rawData && Array.isArray(rawData.data)) {
          const name = typeof rawData.name === 'string' ? rawData.name : '数据序列';
          const data = rawData.data.map((v: unknown) => (typeof v === 'number' ? v : Number(v)));
          const option = {
            title: { text: name, left: 'center' },
            tooltip: { trigger: 'axis' },
            xAxis: { type: 'category', data: data.map((_: number, i: number) => i + 1), name: '序号' },
            yAxis: { type: 'value' },
            series: [{ name, type: 'line', data, smooth: true }],
          };
          return <ChartBlock optionStr={JSON.stringify(option)} />;
        }
      } catch {
        // fall through to default code rendering
      }
      return <code>{codeStr}</code>;
    }
    if (lang === 'data' || lang === 'json') {
      try {
        const parsed = JSON.parse(codeStr);
        return (
          <StructuredDataBlock
            data={parsed}
            workspaceId={workspaceId}
            turnId={turnId}
            onSaved={onConclusionSaved}
          />
        );
      } catch {
        return (
          <details style={{ margin: '8px 0' }}>
            <summary style={{ cursor: 'pointer', fontSize: 12, color: '#8c8c8c' }}>
              {'结构化数据（点击展开）'}
            </summary>
            <pre style={{ fontSize: 10, maxHeight: 300, overflow: 'auto', background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
              <code>{codeStr}</code>
            </pre>
          </details>
        );
      }
    }
    return <code>{codeStr}</code>;
  };

  return (
    <div style={{ padding: '0 4px' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <Text strong>
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
                  const isBlock =
                    lang === 'chart-ref' ||
                    lang === 'chart' ||
                    lang === 'echarts' ||
                    lang === 'describe_series' ||
                    lang === 'describe-series' ||
                    lang === 'describeSeries' ||
                    lang === 'data' ||
                    lang === 'json';
                  if (!isBlock) {
                    return <code className={className}>{children}</code>;
                  }
                  const blockType = detectBlockType(lang) as BlockType;
                  const blockIndex = blockIndexRef.current++;
                  return (
                    <ReportBlockWrapper
                      key={`block-${blockIndex}`}
                      blockType={blockType}
                      codeStr={codeStr}
                      blockIndex={blockIndex}
                      turnInfo={turnInfo}
                      sampleData={detail.fact_samples}
                      lang={lang}
                    >
                      {renderInnerBlock(lang, codeStr)}
                    </ReportBlockWrapper>
                  );
                },
                table({ children }) {
                  const blockIndex = blockIndexRef.current++;
                  const { columns, rows } = extractTableFromChildren(children);
                  return (
                    <ReportBlockWrapper
                      key={`block-${blockIndex}`}
                      blockType="table"
                      codeStr=""
                      blockIndex={blockIndex}
                      turnInfo={turnInfo}
                      snapshotOverride={buildTableSnapshot(columns, rows)}
                      title="数据表格"
                    >
                      <table>{children}</table>
                    </ReportBlockWrapper>
                  );
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
    } catch (_err) {
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

// ============================================================
// Markdown table → {columns, rows} extraction (for conclusion-bar push)
// ============================================================

/** Recursively extract plain text from a React node tree. */
function reactNodeToText(node: ReactNode): string {
  if (node == null || node === false || node === true) return '';
  if (typeof node === 'string') return node;
  if (typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(reactNodeToText).join('');
  if (typeof node === 'object' && 'props' in (node as object)) {
    const el = node as ReactElement<{ children?: ReactNode }>;
    return reactNodeToText(el.props.children);
  }
  return '';
}

/**
 * Extract {columns, rows} from a ReactMarkdown `table` element's children.
 * Walks thead > tr > th for columns and tbody > tr > td for rows.
 */
function extractTableFromChildren(
  tableChildren: ReactNode,
): { columns: string[]; rows: unknown[][] } {
  const columns: string[] = [];
  const rows: unknown[][] = [];

  const visit = (node: ReactNode): void => {
    if (node == null || node === false || node === true) return;
    if (Array.isArray(node)) {
      node.forEach(visit);
      return;
    }
    if (typeof node === 'object' && 'props' in (node as object)) {
      const el = node as ReactElement<{ children?: ReactNode }>;
      const type = el.type;
      if (type === 'thead' || type === 'tbody') {
        visit(el.props.children);
        return;
      }
      if (type === 'tr') {
        const rawCells = el.props.children;
        const cells = Array.isArray(rawCells) ? rawCells : [rawCells];
        const values: string[] = [];
        let isHeader = false;
        for (const c of cells) {
          if (
            c != null &&
            typeof c === 'object' &&
            'props' in (c as object) &&
            (c as ReactElement).type === 'th'
          ) {
            isHeader = true;
          }
          values.push(reactNodeToText(c));
        }
        if (isHeader) {
          columns.push(...values);
        } else {
          rows.push(values);
        }
        return;
      }
      visit(el.props.children);
    }
  };

  visit(tableChildren);
  return { columns, rows };
}
