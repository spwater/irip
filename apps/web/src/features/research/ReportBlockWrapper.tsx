/**
 * ReportBlockWrapper — 报告区块包装器，注入"推送到结论栏"按钮。
 *
 * 参照 BlockWrapper.tsx 模式：hover 显示操作按钮，通过 TanStack Query 缓存
 * 判断是否已推送（turn_id + block_index 匹配，允许多次推送 → 按钮变"再推送"）。
 *
 * 点击时调 buildContentSnapshot() 构建 content_snapshot → useMutation 推送 →
 * invalidateQueries(['conclusion-bar-items', workspaceId])。
 */
import { Button, Tooltip, message } from 'antd';
import { PushpinOutlined, PushpinFilled } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import {
  apiListBarItems,
  apiPushBarItem,
  type BarBlockType,
} from '@/api/researchConclusionBar';
import {
  buildContentSnapshot,
  detectBlockType,
  type BlockType,
} from './blockUtils';
import type { FactSample } from './chartRefResolver';

/** 区块类型中文标签 */
export const BLOCK_TYPE_LABELS: Record<BlockType, string> = {
  echarts: 'ECharts 图表',
  chart_ref: '引用图表',
  structured: '结构化数据',
  table: '数据表格',
  text: '文本',
};

/** 推送溯源信息 */
export interface TurnInfo {
  workspaceId: string;
  turnId: string;
  turnNumber: number;
  snapshotNumber: number | null;
  questionText: string;
}

interface Props {
  /** 区块类型（由 detectBlockType 判定，或外部直接传入） */
  blockType: BlockType;
  /** 代码块原始字符串 */
  codeStr: string;
  /** 轮次溯源信息 */
  turnInfo: TurnInfo;
  /** 区块在报告内的序号（从 0 开始） */
  blockIndex: number;
  /** 已加载的样品数据（用于 chart_ref 解析） */
  sampleData?: FactSample[] | null;
  /** 原始 code 语言（用于 describe_series 等特殊处理） */
  lang?: string;
  /** 区块标题（默认用 BLOCK_TYPE_LABELS） */
  title?: string;
  /** 表格前面的文字（用于生成结论标题） */
  precedingText?: string;
  /** 预构建的 content_snapshot（用于 table 等非 code 块，优先于 codeStr 构建） */
  snapshotOverride?: Record<string, unknown>;
  /** 区块内容 */
  children: React.ReactNode;
}

export function ReportBlockWrapper({
  blockType,
  codeStr,
  turnInfo,
  blockIndex,
  sampleData,
  lang,
  title,
  precedingText,
  snapshotOverride,
  children,
}: Props): JSX.Element {
  const queryClient = useQueryClient();
  const blockId = `${turnInfo.turnId}-${blockIndex}`;

  // 查询当前工作空间结论栏条目，判断此块是否已推送
  const { data: barItems } = useQuery({
    queryKey: ['conclusion-bar-items', turnInfo.workspaceId],
    queryFn: () => apiListBarItems(turnInfo.workspaceId),
  });

  // 判断当前块是否已推送（turn_id + block_index 匹配）
  const existingCount = useMemo(() => {
    if (!barItems) return 0;
    return barItems.filter(
      (item) =>
        item.turn_id === turnInfo.turnId &&
        (item.source_info?.block_index ?? null) === blockIndex,
    ).length;
  }, [barItems, turnInfo.turnId, blockIndex]);

  const isPushed = existingCount > 0;

  const pushMutation = useMutation({
    mutationFn: async () => {
      const snapshot =
        snapshotOverride ?? buildContentSnapshot(blockType, codeStr, sampleData, lang);
      return apiPushBarItem(turnInfo.workspaceId, turnInfo.turnId, {
        block_type: blockType as BarBlockType,
        title: title ?? BLOCK_TYPE_LABELS[blockType] ?? blockType,
        content_snapshot: snapshot,
        block_index: blockIndex,
        source_info: {
          turn_number: turnInfo.turnNumber,
          snapshot_number: turnInfo.snapshotNumber,
          question_text: turnInfo.questionText,
          block_index: blockIndex,
          preceding_text: precedingText ?? '',
        },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['conclusion-bar-items', turnInfo.workspaceId],
      });
      message.success(isPushed ? '已再次推送' : '已推送到结论栏');
    },
    onError: (err) => {
      message.error(err instanceof Error ? err.message : '推送失败');
    },
  });

  const handlePush = (): void => {
    pushMutation.mutate();
  };

  return (
    <div
      data-block-id={blockId}
      style={{
        position: 'relative',
        margin: '8px 0',
        borderRadius: 8,
        transition: 'box-shadow 0.3s',
      }}
      className="research-block"
    >
      {/* 悬浮操作按钮组 */}
      <div
        style={{
          position: 'absolute',
          top: 4,
          right: 4,
          zIndex: 10,
          opacity: 0,
          transition: 'opacity 0.2s',
        }}
        className="research-block-actions"
      >
        <Tooltip title={isPushed ? `已推送 ${existingCount} 次，再推送一次` : '推送到结论栏'}>
          <Button
            size="small"
            type={isPushed ? 'primary' : 'default'}
            icon={isPushed ? <PushpinFilled /> : <PushpinOutlined />}
            loading={pushMutation.isPending}
            onClick={handlePush}
            style={{
              padding: '0 6px',
              minWidth: 28,
              height: 24,
              fontSize: 12,
            }}
          >
            {isPushed ? '再推送' : '推送'}
          </Button>
        </Tooltip>
      </div>

      {/* 块内容 */}
      {children}

      {/* 悬浮样式 */}
      <style>{`
        .research-block:hover .research-block-actions {
          opacity: 1 !important;
        }
      `}</style>
    </div>
  );
}

export default ReportBlockWrapper;

/** 便捷导出 detectBlockType，供 TurnDetailPanel 集成使用 */
export { detectBlockType };
