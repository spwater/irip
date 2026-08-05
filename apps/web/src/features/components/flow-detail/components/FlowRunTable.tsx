/**
 * FlowRunTable — 运行列表 Table（含 output_summary 复杂渲染）。
 *
 * 从 FlowDetail.tsx 提取。
 */

import { Button, Card, Popconfirm, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { PlayCircleOutlined } from '@ant-design/icons';
import { useNavigate } from '@tanstack/react-router';
import type { FlowRunSummary, FlowSummary, ComponentSummary } from '@/api/equipment-flows';
import { fmtTime, RUN_STATUS_COLOR, RUN_STATUS_LABEL } from '../../shared';
import type { CanManageFn } from '../types';
import { FactModal } from '../../FactModal';

const { Text } = Typography;

export interface FlowRunTableProps {
  selectedFlowId: string | null;
  flow: FlowSummary | undefined;
  runs: FlowRunSummary[];
  runsLoading: boolean;
  runPageSize: number;
  setRunPageSize: (size: number) => void;
  activeRunId: string | null;
  compMap: Map<string, ComponentSummary>;
  equipMap: Map<string, string>;
  deptMap: Map<string, string>;
  canManage: CanManageFn;
  onResume: (id: string) => void;
  onCancel: (id: string) => void;
  onDeleteRun: (id: string) => void;
  deleteRunPending: boolean;
  onOpenBatch: () => void;
  projectId?: string;
  factModalOpen: boolean;
  setFactModalOpen: (open: boolean) => void;
  dataRunId: string | null;
  setDataRunId: (id: string | null) => void;
}

export function FlowRunTable(props: FlowRunTableProps): JSX.Element {
  const navigate = useNavigate();
  const {
    selectedFlowId,
    flow,
    runs,
    runsLoading,
    runPageSize,
    setRunPageSize,
    activeRunId,
    compMap,
    equipMap,
    deptMap,
    canManage,
    onResume,
    onCancel,
    onDeleteRun,
    deleteRunPending,
    onOpenBatch,
    projectId,
    factModalOpen,
    setFactModalOpen,
    dataRunId,
    setDataRunId,
  } = props;

  const canExecute = !!selectedFlowId;

  if (!selectedFlowId) return <></>;

  return (
    <Card
      title={
        <Space>
          <span>数据管理</span>
          <Button
            type="primary"
            size="small"
            disabled={!canExecute}
            icon={<PlayCircleOutlined />}
            onClick={onOpenBatch}
          >
            提取
          </Button>
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
      <Table<FlowRunSummary>
        columns={[
          {
            title: '数据名称',
            key: 'data_name',
            width: 280,
            ellipsis: true,
            render: (_: unknown, record: FlowRunSummary) => {
              const taskName = flow?.display_name ?? '';
              const fileName = record.source_filename ?? '';
              const displayName = fileName
                ? `${taskName}-${fileName}`
                : taskName || record.id.slice(0, 8);
              const out = record.output_summary;
              const meta = (out?._metadata ?? {}) as Record<string, unknown>;
              const header = (meta.header ?? meta.metadata ?? {}) as Record<string, unknown>;
              const points = (meta.points ?? []) as {
                name: string;
                value: unknown;
                unit: string | null;
              }[];
              const seriesList = (meta.series ?? []) as {
                name: string;
                columns: string[];
                rows: unknown[][];
              }[];
              const parts: string[] = [];
              if (Object.keys(header).length > 0) {
                parts.push('=== 标头 ===');
                parts.push(JSON.stringify(header, null, 2));
              }
              if (points.length > 0) {
                parts.push('=== 指标（前 3 个） ===');
                parts.push(JSON.stringify(points.slice(0, 3), null, 2));
              }
              if (seriesList.length > 0) {
                parts.push(`=== 序列（${seriesList.length} 组） ===`);
                for (const s of seriesList.slice(0, 2)) {
                  parts.push(`${s.name}: ${s.rows?.length ?? 0} 行`);
                }
              }
              const previewText = parts.join('\n');
              const clickable = record.persisted_as_fact && record.fact_id;
              return (
                <Tooltip
                  title={
                    previewText ? (
                      <pre style={{ fontSize: 11, maxHeight: 300, overflow: 'auto', margin: 0 }}>
                        {previewText}
                      </pre>
                    ) : (
                      '暂无输出数据'
                    )
                  }
                  placement="rightTop"
                  overlayStyle={{ maxWidth: 500 }}
                >
                  <Text
                    style={{
                      fontFamily: 'monospace',
                      fontSize: 12,
                      cursor: clickable ? 'pointer' : 'default',
                      color: clickable ? 'var(--ocean-action-primary)' : 'inherit',
                    }}
                    onClick={(e: React.MouseEvent) => {
                      if (clickable && record.fact_id) {
                        e.stopPropagation();
                        void navigate({
                          to: '/facts/$factId',
                          params: { factId: record.fact_id },
                          search: projectId ? { project: projectId } : undefined,
                        });
                      }
                    }}
                  >
                    {displayName}
                  </Text>
                </Tooltip>
              );
            },
          },
          {
            title: '数据来源',
            key: 'component',
            width: 280,
            render: () => {
              const node = (flow?.latest_version?.nodes ?? [])[0] as
                | { component_name?: string }
                | undefined;
              if (!node?.component_name) return <Text type="secondary">-</Text>;
              const comp = compMap.get(node.component_name);
              const eqName = comp?.equipment_id ? equipMap.get(comp.equipment_id) : null;
              return (
                <Space size={4}>
                  <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                    {comp?.display_name ?? node.component_name}
                  </Tag>
                  {eqName && (
                    <>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        →
                      </Text>
                      <Tag color="cyan" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                        {eqName}
                      </Tag>
                    </>
                  )}
                </Space>
              );
            },
          },
          {
            title: '状态',
            dataIndex: 'status',
            key: 'status',
            width: 100,
            render: (s: string, record: FlowRunSummary) =>
              s === 'failed' && record.error_message ? (
                <Tooltip title={record.error_message}>
                  <Tag color={RUN_STATUS_COLOR[s] ?? 'default'}>{RUN_STATUS_LABEL[s] ?? s}</Tag>
                </Tooltip>
              ) : (
                <Tag color={RUN_STATUS_COLOR[s] ?? 'default'}>{RUN_STATUS_LABEL[s] ?? s}</Tag>
              ),
          },
          {
            title: '执行人',
            dataIndex: 'operator',
            key: 'operator',
            width: 100,
            render: (v: string | null) => v ?? <Text type="secondary">-</Text>,
          },
          {
            title: '创建时间',
            dataIndex: 'created_at',
            key: 'created_at',
            width: 180,
            render: (v: string) => fmtTime(v),
          },
          {
            title: '耗时',
            key: 'duration',
            width: 100,
            render: (_: unknown, record: FlowRunSummary) => {
              if (!record.created_at || !record.completed_at) return '-';
              const ms = new Date(record.completed_at).getTime() - new Date(record.created_at).getTime();
              if (ms < 1000) return `${ms}ms`;
              return `${(ms / 1000).toFixed(1)}s`;
            },
          },
          {
            title: '已存',
            key: 'persisted',
            width: 60,
            align: 'center' as const,
            render: (_: unknown, record: FlowRunSummary) =>
              record.persisted_as_fact ? (
                <span style={{ color: 'var(--ocean-status-success)', fontWeight: 'bold', fontSize: 16 }}>&#10003;</span>
              ) : null,
          },
          {
            title: '操作',
            key: 'action',
            width: 200,
            render: (_: unknown, record: FlowRunSummary) => (
              <Space size="small">
                {record.status === 'succeeded' && (
                  <Button
                    type="link"
                    size="small"
                    disabled={!canManage(flow)}
                    onClick={() => {
                      if (!canManage(flow)) return;
                      setDataRunId(record.id);
                      setFactModalOpen(true);
                    }}
                  >
                    数据入库
                  </Button>
                )}
                {record.status === 'failed' && (
                  <Popconfirm
                    title="确认重试？"
                    onConfirm={() => onResume(record.id)}
                    okText="确定"
                    cancelText="取消"
                  >
                    <Button type="link" size="small">
                      继续
                    </Button>
                  </Popconfirm>
                )}
                {activeRunId === record.id &&
                  record.status !== 'pending' &&
                  record.status !== 'succeeded' &&
                  record.status !== 'cancelled' &&
                  record.status !== 'failed' && (
                    <>
                      <Popconfirm
                        title="确认继续？"
                        onConfirm={() => onResume(record.id)}
                        okText="确定"
                        cancelText="取消"
                      >
                        <Button type="link" size="small">
                          继续
                        </Button>
                      </Popconfirm>
                      <Popconfirm
                        title="确认取消？"
                        onConfirm={() => onCancel(record.id)}
                        okText="确定"
                        cancelText="取消"
                      >
                        <Button type="link" size="small" danger>
                          取消
                        </Button>
                      </Popconfirm>
                    </>
                  )}
                <Popconfirm
                  title="确定删除该运行记录？"
                  description="将同时删除其所有节点执行记录，不可撤销"
                  onConfirm={() => onDeleteRun(record.id)}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                >
                  <Button type="link" size="small" danger loading={deleteRunPending}>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
        dataSource={runs}
        rowKey="id"
        loading={runsLoading}
        pagination={{
          pageSize: runPageSize,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          onShowSizeChange: (_: number, size: number) => setRunPageSize(size),
        }}
        size="small"
        style={{ marginBottom: 16 }}
      />

      <FactModal
        runId={dataRunId}
        flow={flow}
        deptMap={deptMap}
        compMap={compMap}
        open={factModalOpen}
        onClose={() => setFactModalOpen(false)}
      />
    </Card>
  );
}
