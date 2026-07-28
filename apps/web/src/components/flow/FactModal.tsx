/**
 * 数据入库 Modal — 从 FlowDetail.tsx 提取
 *
 * 显示执行结果数据（metadata + 全部行），支持导出 JSON 和写入事实。
 * 顶部展示任务信息卡片（任务名称、任务来源、实验对象、数据接口、创建时间）。
 */

import {
  Button,
  Input,
  Space,
  Typography,
  message,
} from 'antd';
import { useState, useEffect } from 'react';
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
import { FocusModal, DetailSection } from '@/components/ui';
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
  const allRows = (meta.data ?? meta.all_rows ?? meta.preview_rows ?? meta.rows ?? []) as Record<string, unknown>[];
  const header = (meta.metadata ?? meta.header ?? {}) as Record<string, unknown>;

  // 可编辑的数据
  const [headerText, setHeaderText] = useState('');
  const [dataText, setDataText] = useState('');

  // 数据加载后初始化编辑框
  useEffect(() => {
    if (open && runDetail && allRows.length > 0) {
      setHeaderText(JSON.stringify(header, null, 2));
      setDataText(JSON.stringify(allRows, null, 2));
    }
  }, [open, runDetail]);  // eslint-disable-line react-hooks/exhaustive-deps

  // 写入事实 Mutation
  const persistFactMutation = useMutation({
    mutationFn: () => {
      // 解析编辑后的数据
      let customData: { metadata: Record<string, unknown>; data: Record<string, unknown>[] } | undefined;
      try {
        const parsedHeader = JSON.parse(headerText);
        const parsedData = JSON.parse(dataText);
        customData = { metadata: parsedHeader, data: parsedData };
      } catch {
        // 解析失败用原始数据
      }
      return apiPersistRunAsFact(runId!, {
        object_id: factObjectId!,
        template_version_id: null,
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
      <div style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '12px 24px',
        padding: '12px 16px',
        background: 'rgba(240, 250, 251, 0.72)',
        borderRadius: 6,
        marginBottom: 16,
      }}>
        <div><Text type="secondary">任务名称：</Text><Text strong>{taskName}</Text></div>
        <div><Text type="secondary">任务来源：</Text><Text>{deptName}</Text></div>
        <div><Text type="secondary">实验对象：</Text><Text>{expObjDisplay}</Text></div>
        <div><Text type="secondary">数据接口：</Text><Text>{compDisplay}</Text></div>
        <div><Text type="secondary">创建时间：</Text><Text>{createdTime}</Text></div>
      </div>

      {/* metadata 区域 */}
      <DetailSection title="Metadata" technical>
        <Input.TextArea
          value={headerText}
          onChange={(e) => setHeaderText(e.target.value)}
          rows={6}
          style={{
            fontFamily: 'monospace',
            fontSize: 13,
          }}
        />
      </DetailSection>

      {/* 全部数据区域 */}
      <DetailSection
        title="数据（可编辑）"
        technical
        extra={
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
        }
      >
        <Input.TextArea
          value={dataText}
          onChange={(e) => setDataText(e.target.value)}
          rows={16}
          style={{
            fontFamily: 'monospace',
            fontSize: 13,
          }}
        />
      </DetailSection>
    </FocusModal>
  );
}
