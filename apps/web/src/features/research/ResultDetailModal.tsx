/**
 * ResultDetailModal — 发布成果详情 Modal（width=1000）。
 *
 * 布局参考 FactDetail.tsx：Row gutter=16, Col span=10/14。
 * - 左侧（span=10）：数据溯源信息（来源结论 ID、来源轮次、分析问题、创建时间）
 * - 右侧（span=14）：数据预览（Tabs: 元数据 / 单点数据 / 序列数据）
 *   - metadata 用 Descriptions 展示
 *   - points 用 Table 展示
 *   - series 每个 as Card + Table
 */
import { useMemo } from 'react';
import {
  Card,
  Col,
  Descriptions,
  Modal,
  Row,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';
import { apiGetResultDetail } from '@/api/researchResults';
import { DetailSection } from '@/shared/ui';

const { Text } = Typography;

/** 把 UTC 时间字符串转成本地时间显示 */
function fmtTime(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString('zh-CN', { hour12: false });
}

interface Props {
  workspaceId: string;
  resultId: string | null;
  open: boolean;
  onClose: () => void;
}

export function ResultDetailModal({
  workspaceId,
  resultId,
  open,
  onClose,
}: Props): JSX.Element {
  const { data: detail, isLoading } = useQuery({
    queryKey: ['research-result-detail', workspaceId, resultId],
    queryFn: () => apiGetResultDetail(workspaceId, resultId!),
    enabled: open && !!resultId,
  });

  const version = detail?.version ?? null;
  const summary = (version?.summary ?? {}) as Record<string, unknown>;
  const metadata = (summary.metadata as Record<string, unknown> | undefined) ?? {};
  const points = (summary.points as Array<Record<string, unknown>> | undefined) ?? [];
  const seriesList = (summary.series as Array<Record<string, unknown>> | undefined) ?? [];

  // 从 metadata 中提取溯源信息
  const sourceTurns = useMemo(() => {
    const turns = metadata.source_turns;
    return Array.isArray(turns) ? (turns as number[]) : [];
  }, [metadata]);

  const analysisQuestions = useMemo(() => {
    const questions = metadata.analysis_questions;
    return Array.isArray(questions) ? (questions as string[]) : [];
  }, [metadata]);

  // 单点数据表格列
  const pointColumns: ColumnsType<Record<string, unknown>> = [
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '值',
      dataIndex: 'value',
      key: 'value',
      ellipsis: true,
      render: (val: unknown) => {
        if (val === null || val === undefined) return '-';
        if (typeof val === 'number') return val;
        return String(val);
      },
    },
    {
      title: '单位',
      dataIndex: 'unit',
      key: 'unit',
      ellipsis: true,
      render: (val: unknown) => (val === null || val === undefined ? '-' : String(val)),
    },
  ];

  return (
    <Modal
      title={detail?.name ?? '成果详情'}
      open={open}
      onCancel={onClose}
      footer={null}
      width={1000}
      destroyOnClose
    >
      {isLoading || !detail ? (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin tip="加载中…" />
        </div>
      ) : (
        <Row gutter={16}>
          {/* 左侧：数据溯源信息 */}
          <Col span={10}>
            <DetailSection title="发布数据来源">
              <Descriptions bordered column={1} size="small">
                <Descriptions.Item label="成果 ID">
                  <Text copyable code style={{ fontSize: 12 }}>
                    {detail.id}
                  </Text>
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Tag color={detail.status === 'published' ? 'green' : 'default'}>
                    {detail.status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="来源轮次">
                  {sourceTurns.length > 0
                    ? sourceTurns.map((t) => (
                        <Tag key={t} color="blue" style={{ margin: 2 }}>
                          #{t}
                        </Tag>
                      ))
                    : '-'}
                </Descriptions.Item>
                <Descriptions.Item label="分析问题">
                  {analysisQuestions.length > 0 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {analysisQuestions.map((q, i) => (
                        <Text key={i} style={{ fontSize: 12 }}>
                          {i + 1}. {q}
                        </Text>
                      ))}
                    </div>
                  ) : (
                    '-'
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="发布时间">
                  {fmtTime(version?.published_at)}
                </Descriptions.Item>
              </Descriptions>
            </DetailSection>
          </Col>

          {/* 右侧：数据预览 */}
          <Col span={14}>
            <DetailSection
              title={`数据预览（${points.length} 个指标，${seriesList.length} 组序列）`}
            >
              <Tabs
                defaultActiveKey="metadata"
                size="small"
                items={[
                  {
                    key: 'metadata',
                    label: '元数据',
                    children:
                      Object.keys(metadata).length > 0 ? (
                        <Descriptions bordered column={1} size="small">
                          {Object.entries(metadata).map(([k, v]) => (
                            <Descriptions.Item key={k} label={k}>
                              {v === null || v === undefined
                                ? '-'
                                : Array.isArray(v)
                                  ? v.join(', ')
                                  : typeof v === 'object'
                                    ? JSON.stringify(v)
                                    : String(v)}
                            </Descriptions.Item>
                          ))}
                        </Descriptions>
                      ) : (
                        <Text type="secondary">暂无元数据</Text>
                      ),
                  },
                  {
                    key: 'points',
                    label: `单点数据（${points.length}）`,
                    children:
                      points.length > 0 ? (
                        <Table
                          columns={pointColumns}
                          dataSource={points}
                          rowKey={(_, idx) => String(idx)}
                          size="small"
                          pagination={false}
                          scroll={{ y: 400 }}
                        />
                      ) : (
                        <Text type="secondary">暂无单点数据</Text>
                      ),
                  },
                  {
                    key: 'series',
                    label: `序列数据（${seriesList.length}）`,
                    children:
                      seriesList.length > 0 ? (
                        seriesList.map((s, i) => {
                          const name = (s.name as string) ?? `序列 ${i + 1}`;
                          const columns = (s.columns as string[]) ?? [];
                          const rows = (s.rows as unknown[][]) ?? [];
                          return (
                            <Card
                              key={i}
                              size="small"
                              title={name}
                              style={{ marginBottom: 12 }}
                            >
                              <Table
                                size="small"
                                pagination={false}
                                rowKey={(_, idx) => String(idx)}
                                dataSource={rows.map((row, ri) => {
                                  const obj: Record<string, unknown> = { _key: ri };
                                  columns.forEach((c, ci) => {
                                    obj[c] = row[ci];
                                  });
                                  return obj;
                                })}
                                columns={columns.map((c) => ({
                                  title: c,
                                  dataIndex: c,
                                  key: c,
                                  ellipsis: true,
                                  render: (val: unknown) => {
                                    if (val === null || val === undefined) return '-';
                                    if (typeof val === 'number') return val;
                                    return String(val);
                                  },
                                }))}
                              />
                            </Card>
                          );
                        })
                      ) : (
                        <Text type="secondary">暂无序列数据</Text>
                      ),
                  },
                ]}
              />
            </DetailSection>
          </Col>
        </Row>
      )}
    </Modal>
  );
}

export default ResultDetailModal;
