/**
 * 内容块包装器组件。
 *
 * 为 AI 消息中的可操作内容块（图表 / 表格 / 结论 / 公式）提供：
 * 1. 稳定的 DOM 标识（data-block-id="{messageId}-{blockIndex}"）；
 * 2. 右上角悬浮操作按钮组（加入橱窗 / 已加入）；
 * 3. 加入状态一致性（通过 TanStack Query 缓存判断是否已加入）。
 */
import { Button, Tooltip, message } from 'antd';
import { StarOutlined, StarFilled } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import {
  apiAddShowcaseItem,
  apiDeleteShowcaseItem,
  type DataSourceInfo,
  type ShowcaseBlockType,
} from '@/api/showcase';
import { apiListShowcaseItems } from '@/api/showcase';

/** 块类型标签中文映射 */
export const BLOCK_TYPE_LABELS: Record<ShowcaseBlockType, string> = {
  echarts: 'ECharts 图表',
  plotly: 'Plotly 图表',
  table: '数据表格',
  conclusion: '分析结论',
  formula: '计算公式',
  text: '文本',
};

/**
 * 从 system_context 解析数据来源信息。
 * 提取样品标签（### 样品: XXX）、任务名称、检测字段等。
 */
export function parseDataSource(
  systemContext: string | null | undefined,
): DataSourceInfo {
  const ds: DataSourceInfo = {
    sample_labels: [],
    task_name: '',
    fields: [],
    source_tag: '实验数据',
    data_range: '',
  };
  if (!systemContext) return ds;

  // 提取样品标签：`### 样品: XXX` 模式
  const sampleMatches = systemContext.match(/### 样品: (.+)/g) || [];
  ds.sample_labels = sampleMatches
    .map((s) => s.replace('### 样品: ', '').trim())
    .filter(Boolean);

  // 数据范围：用样品标签拼接
  if (ds.sample_labels.length > 0) {
    ds.data_range =
      ds.sample_labels.length === 1
        ? ds.sample_labels[0]
        : `${ds.sample_labels[0]}-${ds.sample_labels[ds.sample_labels.length - 1]}`;
  }

  // 提取任务名称：system_context 中 JSON 块的 metadata.task_name 或顶层提示
  // 格式示例: "以下是实验数据，请基于此数据回答用户的问题："
  // JSON 内可能有 metadata.task_name 字段
  const taskNameMatch = systemContext.match(/"task_name"\s*:\s*"([^"]+)"/);
  if (taskNameMatch) {
    ds.task_name = taskNameMatch[1].trim();
  }

  // 提取检测字段/指标：从 JSON metadata 中查找 fields / properties / column_names
  const fieldsMatch = systemContext.match(/"(?:fields|properties|column_names|indicators)"\s*:\s*\[([^\]]*)\]/);
  if (fieldsMatch) {
    const inner = fieldsMatch[1];
    const fieldStrings = inner.match(/"([^"]+)"/g) || [];
    ds.fields = fieldStrings
      .map((s) => s.replace(/"/g, '').trim())
      .filter(Boolean);
  }

  return ds;
}

export function BlockWrapper({
  messageId,
  blockIndex,
  blockType,
  conversationId,
  systemContext,
  contentSnapshot,
  children,
}: {
  /** 所属消息 ID */
  messageId: string;
  /** 块在消息内的序号（从 0 开始） */
  blockIndex: number;
  /** 块类型 */
  blockType: ShowcaseBlockType;
  /** 当前对话 ID（用于查询橱窗缓存 + 加入橱窗） */
  conversationId: string | null;
  /** 当前对话的 system_context（用于解析 data_source） */
  systemContext: string | null | undefined;
  /** 块内容快照（加入橱窗时保存的原始内容） */
  contentSnapshot: string;
  /** 块内容 */
  children: React.ReactNode;
}): JSX.Element {
  const queryClient = useQueryClient();
  const blockId = `${messageId}-${blockIndex}`;

  // 查询当前对话的橱窗列表，判断此块是否已加入
  const { data: showcaseItems } = useQuery({
    queryKey: ['showcase-items', conversationId],
    queryFn: () => apiListShowcaseItems(conversationId!),
    enabled: !!conversationId,
  });

  // 判断当前块是否已加入橱窗
  const existingItem = useMemo(() => {
    if (!showcaseItems) return null;
    return (
      showcaseItems.find(
        (item) =>
          item.source_message_id === messageId &&
          item.source_block_index === blockIndex,
      ) ?? null
    );
  }, [showcaseItems, messageId, blockIndex]);

  const isAdded = existingItem !== null;

  const handleAddToShowcase = async (): Promise<void> => {
    if (!conversationId) {
      message.warning('请先选择或创建对话');
      return;
    }
    if (isAdded && existingItem) {
      // 已加入 → 取消留存（删除卡片）
      try {
        await apiDeleteShowcaseItem(existingItem.id);
        void queryClient.invalidateQueries({
          queryKey: ['showcase-items', conversationId],
        });
        message.success('已从橱窗移除');
      } catch (err) {
        message.error(
          err instanceof Error ? err.message : '操作失败',
        );
      }
      return;
    }

    const dataSource = parseDataSource(systemContext);
    const title = `${BLOCK_TYPE_LABELS[blockType] ?? blockType}`;
    try {
      await apiAddShowcaseItem(conversationId, {
        block_type: blockType,
        title,
        content_snapshot: contentSnapshot,
        source_message_id: messageId,
        source_block_index: blockIndex,
        data_source: dataSource,
      });
      void queryClient.invalidateQueries({
        queryKey: ['showcase-items', conversationId],
      });
      message.success('已加入橱窗');
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : '加入橱窗失败',
      );
    }
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
      className="ai-content-block"
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
        className="block-actions"
      >
        <Tooltip title={isAdded ? '已加入橱窗，点击移除' : '加入橱窗'}>
          <Button
            size="small"
            type={isAdded ? 'primary' : 'default'}
            icon={isAdded ? <StarFilled /> : <StarOutlined />}
            onClick={handleAddToShowcase}
            style={{
              padding: '0 6px',
              minWidth: 28,
              height: 24,
              fontSize: 12,
            }}
          >
            {isAdded ? '已加入' : '加入橱窗'}
          </Button>
        </Tooltip>
      </div>

      {/* 块内容 */}
      {children}

      {/* 悬浮样式 */}
      <style>{`
        .ai-content-block:hover .block-actions {
          opacity: 1 !important;
        }
        .ai-content-block.highlight {
          box-shadow: 0 0 0 3px rgba(22, 134, 174, 0.5);
          background: rgba(22, 134, 174, 0.08);
          border-radius: 8px;
          animation: blockHighlight 0.4s ease;
        }
        @keyframes blockHighlight {
          0% { box-shadow: 0 0 0 0 rgba(22, 134, 174, 0.6); }
          50% { box-shadow: 0 0 0 6px rgba(22, 134, 174, 0.3); }
          100% { box-shadow: 0 0 0 3px rgba(22, 134, 174, 0.5); }
        }
      `}</style>
    </div>
  );
}

export default BlockWrapper;
