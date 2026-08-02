/**
 * 数据入库 Modal — 从 FlowDetail.tsx 提取
 *
 * 显示执行结果数据（metadata + 全部行），支持导出 JSON 和写入事实。
 * 顶部展示任务信息卡片（任务名称、任务来源、实验对象、数据接口、创建时间）。
 */

import {
  Button,
  Card,
  Descriptions,
  Input,
  Modal,
  Space,
  Table,
  Typography,
  message,
} from 'antd';
import { useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  apiGetFlowRun,
  apiPersistRunAsFact,
  type ComponentSummary,
  type FlowNodeSchema,
  type FlowSummary,
} from '@/api/equipment-flows';
import { apiListObjects } from '@/api/standards-objects';
import { extractApiError } from '@/api/types';
import { fmtTime } from './shared';
import { FocusModal } from '@/shared/ui';
import { OceanPanel } from '@/shared/ui';

const { Text } = Typography;

export function FactModal({
  runId,
  flow,
  deptMap,
  compMap,
  open,
  onClose,
}: {
  runId: string | null;
  flow: FlowSummary | undefined;
  deptMap: Map<string, string>;
  compMap: Map<string, ComponentSummary>;
  open: boolean;
  onClose: () => void;
}): JSX.Element {
  const queryClient = useQueryClient();

  // 从流程节点的 params 中提取 experimental_object_code
  const flowNodes = (flow?.latest_version?.nodes ?? []) as FlowNodeSchema[];
  const expObjectCode = flowNodes
    .map((n) => (n.params as Record<string, unknown> | undefined)?.experimental_object_code as string | undefined)
    .find((v): v is string => Boolean(v));

  // 查询工业对象（找到 code 对应的 object_id）
  const { data: objectsData } = useQuery({
    queryKey: ['objects-for-fact'],
    queryFn: () => apiListObjects({ page_size: 100 }),
    enabled: open,
  });
  const objects = objectsData?.items ?? [];
  const matchedObject = expObjectCode
    ? objects.find((o) => o.code === expObjectCode)
    : undefined;
  const factObjectId = matchedObject?.id;

  // 查询运行详情获取输出数据
  const { data: runDetail } = useQuery({
    queryKey: ['flow-run', runId],
    queryFn: () => apiGetFlowRun(runId!),
    enabled: open && !!runId,
  });

  const succeededNode = (runDetail?.nodes ?? []).find(
    (n) => n.status === 'succeeded' && n.output_summary,
  );
  const meta = (succeededNode?.output_summary?._metadata ?? {}) as Record<string, unknown>;
  const header = (meta.metadata ?? meta.header ?? {}) as Record<string, unknown>;
  const points = (meta.points ?? []) as { name: string; value: unknown; unit: string | null }[];
  const series = (meta.series ?? []) as { name: string; columns: string[]; rows: unknown[][] }[];

  // 可编辑的数据
  const [headerText, setHeaderText] = useState('');
  const [dataText, setDataText] = useState('');
  const [initialized, setInitialized] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  // 数据加载后初始化编辑框（仅首次加载时，不覆盖用户编辑）
  useEffect(() => {
    if (open && runDetail && (Object.keys(header).length > 0 || points.length > 0 || series.length > 0) && !initialized) {
      setHeaderText(JSON.stringify(header, null, 2));
      setDataText(JSON.stringify({ points, series }, null, 2));
      setInitialized(true);
    }
    if (!open) {
      setInitialized(false);
    }
  }, [open, runDetail, initialized]);  // eslint-disable-line react-hooks/exhaustive-deps

  // 写入事实 Mutation
  const persistFactMutation = useMutation({
    mutationFn: () => {
      // 解析编辑后的数据
      let customData: { metadata: Record<string, unknown>; points: { name: string; value: unknown; unit: string | null }[]; series: unknown[] } | undefined;
      try {
        const parsedHeader = JSON.parse(headerText);
        const parsedData = JSON.parse(dataText);
        if (parsedData && typeof parsedData === 'object' && !Array.isArray(parsedData)) {
          customData = {
            metadata: parsedHeader as Record<string, unknown>,
            points: (parsedData.points ?? []) as { name: string; value: unknown; unit: string | null }[],
            series: (parsedData.series ?? []) as unknown[],
          };
        }
      } catch {
        // 解析失败用原始数据
      }
      return apiPersistRunAsFact(runId!, {
        object_id: factObjectId!,
        custom_data: customData,
      });
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['facts'] });
      void queryClient.invalidateQueries({ queryKey: ['flow-runs'] });
      void queryClient.refetchQueries({ queryKey: ['flow-runs'] });
      onClose();
      message.success(`已写入事实：${data.raw_count} 条观察值（fact_id=${data.fact_id.slice(0, 8)}...）`);
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // 提取任务信息
  const taskName = flow?.display_name ?? '-';
  const deptName = flow?.department_id ? (deptMap.get(flow.department_id) ?? '-') : '-';
  const createdTime = flow ? fmtTime(flow.created_at) : '-';
  // 数据接口（组件名）
  const compNames = Array.from(
    new Set(
      flowNodes
        .map((n) => n.component_name)
        .filter((n): n is string => Boolean(n)),
    ),
  );
  const compDisplay = compNames.map((n) => compMap.get(n)?.display_name ?? n).join(', ') || '-';
  // 实验对象
  const expObjDisplay = matchedObject ? `${matchedObject.display_name}（${matchedObject.code}）` : '-';

  return (
    <FocusModal
      open={open}
      title="执行结果数据"
      onCancel={onClose}
      width={720}
      hideFooter
    >
      {/* 任务信息 */}
      <OceanPanel variant="default" padding="12px 16px" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px 24px' }}>
          <div><Text type="secondary">任务名称：</Text><Text strong>{taskName}</Text></div>
          <div><Text type="secondary">任务来源：</Text><Text>{deptName}</Text></div>
          <div><Text type="secondary">实验对象：</Text><Text>{expObjDisplay}</Text></div>
          <div><Text type="secondary">数据接口：</Text><Text>{compDisplay}</Text></div>
          <div><Text type="secondary">创建时间：</Text><Text>{createdTime}</Text></div>
        </div>
      </OceanPanel>
      {/* metadata 区域 */}
      <Text strong>Metadata</Text>
      <Input.TextArea
        value={headerText}
        onChange={(e) => setHeaderText(e.target.value)}
        rows={6}
        style={{
          fontFamily: 'var(--ocean-font-mono)',
          fontSize: 13,
          marginTop: 4,
          marginBottom: 16,
        }}
      />

      {/* 全部数据区域 */}
      <Space style={{ marginBottom: 4, width: '100%', justifyContent: 'space-between' }}>
        <Text strong>数据（points + series，可编辑）</Text>
        <Space>
          <Button
            size="small"
            onClick={() => setPreviewOpen(true)}
          >
            预览数据
          </Button>
          <Button
            size="small"
            onClick={() => {
              const blob = new Blob([dataText], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `run-${runId?.slice(0, 8)}.json`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            导出 JSON
          </Button>
        </Space>
      </Space>
      <Input.TextArea
        value={dataText}
        onChange={(e) => setDataText(e.target.value)}
        rows={16}
        style={{
          fontFamily: 'var(--ocean-font-mono)',
          fontSize: 13,
          marginTop: 4,
        }}
      />
      {/* 底部操作区 */}
      <Space style={{ width: '100%', justifyContent: 'flex-end', marginTop: 16 }}>
        <Button onClick={onClose}>关闭</Button>
        <Button
          type="primary"
          disabled={!factObjectId}
          loading={persistFactMutation.isPending}
          onClick={() => persistFactMutation.mutate()}
        >
          写入事实
        </Button>
      </Space>

      {/* 数据预览 Modal */}
      <PreviewModal
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        headerText={headerText}
        dataText={dataText}
      />
    </FocusModal>
  );
}

/** 数据预览 Modal — 解析编辑框中的 JSON，格式化展示 metadata/points/series */
function PreviewModal({
  open,
  onClose,
  headerText,
  dataText,
}: {
  open: boolean;
  onClose: () => void;
  headerText: string;
  dataText: string;
}): JSX.Element | null {
  if (!open) return null;

  // 解析当前编辑框内容
  let parsedMeta: Record<string, unknown> = {};
  let parsedPoints: { name: string; value: unknown; unit: string | null }[] = [];
  let parsedSeries: { name: string; columns: string[]; rows: unknown[][] }[] = [];
  let parseError: string | null = null;

  try {
    parsedMeta = headerText ? JSON.parse(headerText) : {};
  } catch {
    parseError = 'Metadata JSON 格式错误';
  }
  try {
    const parsedData = dataText ? JSON.parse(dataText) : {};
    parsedPoints = (parsedData.points ?? []) as typeof parsedPoints;
    parsedSeries = (parsedData.series ?? []) as typeof parsedSeries;
  } catch {
    parseError = parseError ? `${parseError}；数据 JSON 也格式错误` : '数据 JSON 格式错误';
  }

  return (
    <Modal
      title="数据预览"
      open={open}
      onCancel={onClose}
      footer={null}
      width={800}
    >
      {parseError ? (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Text type="danger" style={{ fontSize: 14 }}>{parseError}</Text>
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              请修正编辑框中的 JSON 格式后再预览
            </Text>
          </div>
        </div>
      ) : (
        <div style={{ maxHeight: 600, overflow: 'auto' }}>
          {/* Metadata */}
          <Text strong>元数据（Metadata）</Text>
          <Descriptions
            bordered
            column={1}
            size="small"
            style={{ marginTop: 8, marginBottom: 16 }}
          >
            {Object.keys(parsedMeta).length > 0 ? (
              Object.entries(parsedMeta).map(([k, v]) => (
                <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
              ))
            ) : (
              <Descriptions.Item label="（空）">无元数据</Descriptions.Item>
            )}
          </Descriptions>

          {/* Points */}
          <Text strong>单点数据（Points，{parsedPoints.length} 项）</Text>
          <Table
            size="small"
            style={{ marginTop: 8, marginBottom: 16 }}
            pagination={false}
            rowKey={(_, idx) => String(idx)}
            dataSource={parsedPoints}
            columns={[
              { title: '名称', dataIndex: 'name', key: 'name' },
              { title: '值', dataIndex: 'value', key: 'value' },
              { title: '单位', dataIndex: 'unit', key: 'unit' },
            ]}
          />

          {/* Series */}
          <Text strong>序列数据（Series，{parsedSeries.length} 组）</Text>
          {parsedSeries.length > 0 ? (
            parsedSeries.map((s, i) => (
              <Card key={i} size="small" title={s.name ?? `序列 ${i + 1}`} style={{ marginTop: 8, marginBottom: 8 }}>
                <Table
                  size="small"
                  pagination={false}
                  rowKey={(_, idx) => String(idx)}
                  dataSource={s.rows.map((r, ri) => {
                    const obj: Record<string, unknown> = { _key: ri };
                    (s.columns ?? []).forEach((c, ci) => { obj[c] = r[ci]; });
                    return obj;
                  })}
                  columns={(s.columns ?? []).map((c) => ({
                    title: c,
                    dataIndex: c,
                    key: c,
                    ellipsis: true,
                  }))}
                />
              </Card>
            ))
          ) : (
            <Text type="secondary">无序列数据</Text>
          )}
        </div>
      )}
    </Modal>
  );
}
