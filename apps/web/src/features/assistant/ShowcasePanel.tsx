/**
 * 右栏分析橱窗面板主组件。
 *
 * 功能：
 * - 标题栏："分析橱窗" + 卡片数量 Badge + 收起/展开按钮
 * - 类型筛选（全部/图表/表格/结论/公式）
 * - 卡片列表（拖拽排序）
 * - 底部操作栏（生成摘要）
 * - 空状态引导
 * - 收起态窄边条
 */
import { useState } from 'react';
import {
  Button,
  Card,
  Empty,
  Segmented,
  Space,
  Badge,
  Typography,
  message,
} from 'antd';
import {
  AppstoreOutlined,
  BarChartOutlined,
  TableOutlined,
  BulbOutlined,
  FunctionOutlined,
  FileTextOutlined,
  FileSearchOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  apiListShowcaseItems,
  apiDeleteShowcaseItem,
  apiUpdateShowcaseItem,
  apiGenerateSummary,
  type ShowcaseItem,
  type ShowcaseBlockType,
} from '@/api/showcase';
import { ShowcaseSortableList } from '@/features/assistant/ShowcaseSortableList';
import { SummaryModal } from '@/features/assistant/SummaryModal';

const { Text, Title } = Typography;

/** 筛选选项 */
type FilterType = 'all' | 'chart' | 'table' | 'conclusion' | 'formula';

/** 筛选选项列表 */
const FILTER_OPTIONS: { label: string; value: FilterType; icon: JSX.Element }[] = [
  { label: '全部', value: 'all', icon: <AppstoreOutlined /> },
  { label: '图表', value: 'chart', icon: <BarChartOutlined /> },
  { label: '表格', value: 'table', icon: <TableOutlined /> },
  { label: '结论', value: 'conclusion', icon: <BulbOutlined /> },
  { label: '公式', value: 'formula', icon: <FunctionOutlined /> },
];

/** 判断块类型是否属于图表 */
function isChartType(type: ShowcaseBlockType): boolean {
  return type === 'echarts' || type === 'plotly';
}

/** 筛选卡片列表 */
function filterItems(
  items: ShowcaseItem[],
  filter: FilterType,
): ShowcaseItem[] {
  if (filter === 'all') return items;
  return items.filter((item) => {
    switch (filter) {
      case 'chart':
        return isChartType(item.block_type);
      case 'table':
        return item.block_type === 'table';
      case 'conclusion':
        return item.block_type === 'conclusion' || item.block_type === 'text';
      case 'formula':
        return item.block_type === 'formula';
      default:
        return true;
    }
  });
}

export function ShowcasePanel({
  conversationId,
  conversationTitle,
  collapsed,
  onToggleCollapse,
  onLocateMessage,
}: {
  /** 当前对话 ID */
  conversationId: string | null;
  /** 当前对话标题 */
  conversationTitle: string;
  /** 是否收起 */
  collapsed: boolean;
  /** 收起/展开切换回调 */
  onToggleCollapse: () => void;
  /** 定位原文回调 */
  onLocateMessage: (messageId: string, blockIndex: number) => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<FilterType>('all');
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [summaryMarkdown, setSummaryMarkdown] = useState('');
  const [summaryLoading, setSummaryLoading] = useState(false);

  // 查询橱窗卡片列表
  const { data: items, isLoading } = useQuery({
    queryKey: ['showcase-items', conversationId],
    queryFn: () => apiListShowcaseItems(conversationId!),
    enabled: !!conversationId,
  });

  const allItems: ShowcaseItem[] = items ?? [];
  const filteredItems = filterItems(allItems, filter);

  // 删除卡片 mutation（乐观更新）
  const deleteMutation = useMutation({
    mutationFn: (itemId: string) => apiDeleteShowcaseItem(itemId),
    onMutate: async (itemId: string) => {
      if (!conversationId) return;
      const queryKey = ['showcase-items', conversationId];
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<ShowcaseItem[]>(queryKey);
      queryClient.setQueryData<ShowcaseItem[]>(queryKey, (old) =>
        (old ?? []).filter((i) => i.id !== itemId),
      );
      return { previous };
    },
    onError: (_err, _itemId, context) => {
      if (conversationId && context?.previous) {
        queryClient.setQueryData(
          ['showcase-items', conversationId],
          context.previous,
        );
      }
      message.error('删除失败');
    },
    onSuccess: () => {
      if (conversationId) {
        void queryClient.invalidateQueries({
          queryKey: ['showcase-items', conversationId],
        });
      }
    },
  });

  // 重命名 mutation（乐观更新）
  const renameMutation = useMutation({
    mutationFn: ({ itemId, title }: { itemId: string; title: string }) =>
      apiUpdateShowcaseItem(itemId, { title }),
    onMutate: async ({ itemId, title }) => {
      if (!conversationId) return;
      const queryKey = ['showcase-items', conversationId];
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<ShowcaseItem[]>(queryKey);
      queryClient.setQueryData<ShowcaseItem[]>(queryKey, (old) =>
        (old ?? []).map((i) => (i.id === itemId ? { ...i, title } : i)),
      );
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (conversationId && context?.previous) {
        queryClient.setQueryData(
          ['showcase-items', conversationId],
          context.previous,
        );
      }
      message.error('重命名失败');
    },
    onSuccess: () => {
      if (conversationId) {
        void queryClient.invalidateQueries({
          queryKey: ['showcase-items', conversationId],
        });
      }
    },
  });

  // 生成摘要
  const handleGenerateSummary = async (): Promise<void> => {
    if (!conversationId) return;
    if (allItems.length === 0) {
      message.warning('请先加入内容');
      return;
    }
    setSummaryLoading(true);
    try {
      const result = await apiGenerateSummary(conversationId);
      setSummaryMarkdown(result.markdown);
      setSummaryOpen(true);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '生成摘要失败');
    } finally {
      setSummaryLoading(false);
    }
  };

  // ---- 收起态：窄边条 ----
  if (collapsed) {
    return (
      <div
        style={{
          width: 48,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: '12px 0',
          background: 'var(--ocean-surface-strong, #fff)',
          border: '1px solid var(--ocean-border-subtle)',
          borderRadius: 8,
          cursor: 'pointer',
        }}
        onClick={onToggleCollapse}
        title="展开橱窗"
      >
        <MenuUnfoldOutlined style={{ fontSize: 18, color: 'var(--ocean-text-muted)' }} />
        <div style={{ marginTop: 8, writingMode: 'vertical-rl', fontSize: 12 }}>
          <Badge count={allItems.length} size="small" offset={[0, 0]}>
            <Text style={{ fontSize: 11, color: 'var(--ocean-text-muted)' }}>
              分析橱窗
            </Text>
          </Badge>
        </div>
      </div>
    );
  }

  // ---- 展开态：完整面板 ----
  return (
    <Card
      size="small"
      style={{
        width: 360,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
      }}
      bodyStyle={{
        padding: 0,
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
      title={
        <Space size={8}>
          <Title level={5} style={{ margin: 0 }}>
            分析橱窗
          </Title>
          <Badge count={allItems.length} overflowCount={99} />
        </Space>
      }
      extra={
        <Button
          type="text"
          size="small"
          icon={<MenuFoldOutlined />}
          onClick={onToggleCollapse}
          title="收起橱窗"
        />
      }
    >
      {/* 筛选栏 */}
      {allItems.length > 0 && (
        <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--ocean-border-subtle)' }}>
          <Segmented
            size="small"
            value={filter}
            onChange={(val) => setFilter(val as FilterType)}
            options={FILTER_OPTIONS.map((o) => ({
              label: (
                <Space size={2}>
                  {o.icon}
                  <span style={{ fontSize: 11 }}>{o.label}</span>
                </Space>
              ),
              value: o.value,
            }))}
          />
        </div>
      )}

      {/* 卡片列表区域 */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '8px 12px',
        }}
      >
        {!conversationId ? (
          <Empty
            image={<FileSearchOutlined style={{ fontSize: 40, color: 'var(--ocean-text-muted)' }} />}
            description={
              <Text type="secondary" style={{ fontSize: 12 }}>
                请先选择对话
              </Text>
            }
          />
        ) : isLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              加载中...
            </Text>
          </div>
        ) : filteredItems.length === 0 ? (
          <Empty
            image={<FileSearchOutlined style={{ fontSize: 40, color: 'var(--ocean-text-muted)' }} />}
            description={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {allItems.length === 0
                  ? '在对话中点击「加入橱窗」将重要内容留存到这里'
                  : '当前筛选无匹配卡片'}
              </Text>
            }
          />
        ) : (
          <ShowcaseSortableList
            items={filteredItems}
            allItems={allItems}
            conversationId={conversationId}
            onLocate={onLocateMessage}
            onDelete={(itemId) => deleteMutation.mutate(itemId)}
            onRename={(itemId, title) =>
              renameMutation.mutate({ itemId, title })
            }
          />
        )}
      </div>

      {/* 底部操作栏 */}
      <div
        style={{
          padding: '8px 12px',
          borderTop: '1px solid var(--ocean-border-subtle)',
        }}
      >
        <Button
          block
          type="primary"
          ghost
          icon={<FileTextOutlined />}
          loading={summaryLoading}
          disabled={allItems.length === 0}
          onClick={handleGenerateSummary}
        >
          生成摘要
        </Button>
      </div>

      {/* 摘要预览 Modal */}
      <SummaryModal
        open={summaryOpen}
        markdown={summaryMarkdown}
        title={conversationTitle}
        onClose={() => setSummaryOpen(false)}
      />
    </Card>
  );
}

export default ShowcasePanel;
