/**
 * 数据入库 Modal — 从 FlowDetail.tsx 提取
 *
 * 显示执行结果数据（metadata + 全部行），支持导出 JSON 和写入事实。
 * 顶部展示任务信息卡片（任务名称、任务来源、实验对象、数据接口、创建时间）。
 */

import {
  Button,
  Card,
  Modal,
  Space,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  apiGetFlowRun,
  apiListObjects,
  apiPersistRunAsFact,
  extractApiError,
  type ComponentSummary,
  type FlowNodeSchema,
  type FlowSummary,
} from '@/api/client';
import { fmtTime } from './shared';

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

  const succeededNode = runDetail?.nodes.find(
    (n) => n.status === 'succeeded' && n.output_summary,
  );
  const meta = (succeededNode?.output_summary?._metadata ?? {}) as Record<string, unknown>;
  const allRows = (meta.all_rows ?? meta.preview_rows ?? []) as Record<string, unknown>[];
  const header = (meta.header ?? {}) as Record<string, unknown>;
  const exportData = { metadata: header, data: allRows };

  // 写入事实 Mutation
  const persistFactMutation = useMutation({
    mutationFn: () =>
      apiPersistRunAsFact(runId!, {
        object_id: factObjectId!,
        template_version_id: null,
      }),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['facts'] });
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
    <Modal
      title="执行结果数据"
      open={open}
      onCancel={onClose}
      footer={
        <Space>
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
      }
      width={720}
    >
      {/* 任务信息 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px 24px' }}>
          <div><Text type="secondary">任务名称：</Text><Text strong>{taskName}</Text></div>
          <div><Text type="secondary">任务来源：</Text><Text>{deptName}</Text></div>
          <div><Text type="secondary">实验对象：</Text><Text>{expObjDisplay}</Text></div>
          <div><Text type="secondary">数据接口：</Text><Text>{compDisplay}</Text></div>
          <div><Text type="secondary">创建时间：</Text><Text>{createdTime}</Text></div>
        </div>
      </Card>
      {/* metadata 区域 */}
      <Text strong>Metadata</Text>
      <pre
        style={{
          background: '#f5f5f5',
          padding: 12,
          borderRadius: 6,
          fontSize: 13,
          fontFamily: 'monospace',
          maxHeight: 200,
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          marginTop: 4,
          marginBottom: 16,
        }}
      >
        {JSON.stringify(header, null, 2)}
      </pre>

      {/* 全部数据区域 */}
      <Space style={{ marginBottom: 4, width: '100%', justifyContent: 'space-between' }}>
        <Text strong>数据（{allRows.length} 行）</Text>
        <Button
          size="small"
          onClick={() => {
            const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
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
      <pre
        style={{
          background: '#f5f5f5',
          padding: 12,
          borderRadius: 6,
          fontSize: 13,
          fontFamily: 'monospace',
          maxHeight: 400,
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          marginTop: 4,
        }}
      >
        {JSON.stringify(allRows, null, 2)}
      </pre>
    </Modal>
  );
}
