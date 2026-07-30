/**
 * 橱窗卡片拖拽排序列表容器。
 *
 * 使用 @dnd-kit/sortable 实现拖拽排序，拖拽结束后调用 API 持久化新顺序，
 * 失败时回滚（乐观更新策略）。
 */
import { useQueryClient } from '@tanstack/react-query';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
  useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { message } from 'antd';
import { HolderOutlined } from '@ant-design/icons';
import {
  apiReorderShowcaseItems,
  type ShowcaseItem,
} from '@/api/showcase';
import { ShowcaseCard } from '@/features/assistant/ShowcaseCard';

/** 单个可拖拽卡片项 */
function SortableShowcaseCard({
  item,
  onLocate,
  onDelete,
  onRename,
}: {
  item: ShowcaseItem;
  onLocate: (messageId: string, blockIndex: number) => void;
  onDelete: (itemId: string) => void;
  onRename: (itemId: string, title: string) => void;
}): JSX.Element {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
    >
      <div style={{ display: 'flex', alignItems: 'stretch' }}>
        {/* 拖拽手柄 */}
        <div
          {...listeners}
          style={{
            display: 'flex',
            alignItems: 'center',
            padding: '0 4px',
            cursor: 'grab',
            color: 'var(--ocean-text-muted)',
            fontSize: 16,
            flexShrink: 0,
          }}
          title="拖拽排序"
        >
          <HolderOutlined />
        </div>
        {/* 卡片内容 */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <ShowcaseCard
            item={item}
            onLocate={onLocate}
            onDelete={onDelete}
            onRename={onRename}
          />
        </div>
      </div>
    </div>
  );
}

export function ShowcaseSortableList({
  items,
  allItems,
  conversationId,
  onLocate,
  onDelete,
  onRename,
}: {
  /** 橱窗卡片列表（已筛选、已按 sort_order 排序） */
  items: ShowcaseItem[];
  /** 全量卡片列表（未筛选，用于排序持久化时包含未筛选项） */
  allItems: ShowcaseItem[];
  /** 当前对话 ID */
  conversationId: string;
  /** 定位原文回调 */
  onLocate: (messageId: string, blockIndex: number) => void;
  /** 删除卡片回调 */
  onDelete: (itemId: string) => void;
  /** 重命名回调 */
  onRename: (itemId: string, title: string) => void;
}): JSX.Element {
  const queryClient = useQueryClient();

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 5 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const handleDragEnd = async (event: DragEndEvent): Promise<void> => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    // 在筛选后的列表中找到拖拽位置
    const oldIndex = items.findIndex((i) => i.id === active.id);
    const newIndex = items.findIndex((i) => i.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;

    // 在全量列表中计算最终顺序：将 active 移动到 over 的位置
    // 策略：以 allItems 为基准，把 active 移到 over 之前/之后的位置
    const fullList = [...allItems];
    const activeIdx = fullList.findIndex((i) => i.id === active.id);
    const overIdx = fullList.findIndex((i) => i.id === over.id);
    if (activeIdx === -1 || overIdx === -1) return;

    const [moved] = fullList.splice(activeIdx, 1);
    // 找到 over 在移除后的新位置
    const overIdxAfterRemove = fullList.findIndex((i) => i.id === over.id);
    fullList.splice(overIdxAfterRemove, 0, moved);
    const newOrderIds = fullList.map((i) => i.id);

    // 乐观更新：更新全量缓存中的 sort_order
    const queryKey = ['showcase-items', conversationId];
    const previousData = queryClient.getQueryData<ShowcaseItem[]>(queryKey);
    queryClient.setQueryData<ShowcaseItem[]>(queryKey, (old) => {
      if (!old) return old;
      // 用 fullList 的新顺序重新分配 sort_order
      return fullList.map((item, idx) => ({
        ...item,
        sort_order: idx,
      }));
    });

    try {
      await apiReorderShowcaseItems(conversationId, newOrderIds);
    } catch (err) {
      // 回滚
      if (previousData) {
        queryClient.setQueryData(queryKey, previousData);
      }
      message.error(
        err instanceof Error ? err.message : '排序保存失败',
      );
    }
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={handleDragEnd}
    >
      <SortableContext
        items={items.map((i) => i.id)}
        strategy={verticalListSortingStrategy}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {items.map((item) => (
            <SortableShowcaseCard
              key={item.id}
              item={item}
              onLocate={onLocate}
              onDelete={onDelete}
              onRename={onRename}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}

export default ShowcaseSortableList;
