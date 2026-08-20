/**
 * ConclusionBar — 结论栏面板。
 *
 * 用 useQuery 加载工作空间结论栏条目，Checkbox 勾选状态用 useState<Set>，
 * 底部"生成最终结论"按钮（checkedIds.size > 0 启用），useMutation 调 finalize。
 *
 * finalize 成功后 invalidate ['conclusions', ws] + ['conclusion-bar-items', ws]，
 * 并通过 onFinalized 回调通知父组件切换到结论库 Tab。
 */
import { useState } from 'react';
import { Button, Checkbox, Empty, Spin, Typography, message, Popconfirm } from 'antd';
import { DeleteOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  apiFinalizeConclusion,
  apiListBarItems,
  apiRemoveBarItem,
  genBarIdempotencyKey,
  type BarItem,
} from '@/api/researchConclusionBar';
import { BarItemRenderer } from './BarItemRenderer';

const { Text } = Typography;

interface Props {
  workspaceId: string;
  /** finalize 成功后的回调（用于切换到结论库 Tab） */
  onFinalized?: () => void;
}

export function ConclusionBar({ workspaceId, onFinalized }: Props): JSX.Element {
  const queryClient = useQueryClient();
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());

  const itemsQuery = useQuery({
    queryKey: ['conclusion-bar-items', workspaceId],
    queryFn: () => apiListBarItems(workspaceId),
  });

  const removeMutation = useMutation({
    mutationFn: (itemId: string) => apiRemoveBarItem(workspaceId, itemId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['conclusion-bar-items', workspaceId],
      });
      message.success('已移除');
    },
    onError: (err) => {
      message.error(err instanceof Error ? err.message : '移除失败');
    },
  });

  const finalizeMutation = useMutation({
    mutationFn: (itemIds: string[]) =>
      apiFinalizeConclusion(workspaceId, {
        item_ids: itemIds,
        idempotency_key: genBarIdempotencyKey(),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['conclusion-bar-items', workspaceId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['conclusions', workspaceId],
      });
      setCheckedIds(new Set());
      message.success('已生成最终结论');
      onFinalized?.();
    },
    onError: (err) => {
      message.error(err instanceof Error ? err.message : '生成最终结论失败');
    },
  });

  const handleToggle = (itemId: string): void => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  };

  const handleRemove = (itemId: string): void => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      next.delete(itemId);
      return next;
    });
    removeMutation.mutate(itemId);
  };

  const handleFinalize = (): void => {
    finalizeMutation.mutate(Array.from(checkedIds));
  };

  if (itemsQuery.isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 24 }}>
        <Spin tip="加载中..." />
      </div>
    );
  }

  const items: BarItem[] = itemsQuery.data ?? [];

  if (items.length === 0) {
    return (
      <Empty
        description="暂无推送条目"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    );
  }

  return (
    <div data-testid="conclusion-bar">
      <div style={{ marginBottom: 8 }}>
        <Text type="secondary">
          {'结论栏 ('}{items.length}{' 条)'}
          {checkedIds.size > 0 && (
            <Text type="warning">{' · 已选 '}{checkedIds.size}{' 条'}</Text>
          )}
        </Text>
      </div>

      {items.map((item) => {
        const isChecked = checkedIds.has(item.id);
        return (
          <div
            key={item.id}
            style={{
              padding: '8px 12px',
              marginBottom: 8,
              border: '1px solid #f0f0f0',
              borderRadius: 6,
              background: isChecked ? 'rgba(22, 134, 174, 0.04)' : '#fff',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start' }}>
              <Checkbox
                checked={isChecked}
                onChange={() => handleToggle(item.id)}
                style={{ marginRight: 8, marginTop: 2 }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ marginBottom: 4 }}>
                  <Text strong style={{ fontSize: 13 }}>{item.title}</Text>
                  <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
                    {item.source_info?.turn_number != null
                      ? `轮次 #${item.source_info.turn_number}`
                      : ''}
                  </Text>
                </div>
                <BarItemRenderer item={item} />
              </div>
              <Popconfirm
                title="确认从结论栏移除？"
                onConfirm={() => handleRemove(item.id)}
                okText="移除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button
                  type="text"
                  size="small"
                  icon={<DeleteOutlined />}
                  loading={removeMutation.isPending && removeMutation.variables === item.id}
                  style={{ color: '#999', flexShrink: 0 }}
                />
              </Popconfirm>
            </div>
          </div>
        );
      })}

      {/* 底部"生成最终结论"按钮 */}
      <div style={{ marginTop: 12, textAlign: 'center' }}>
        <Popconfirm
          title={`确认将选中的 ${checkedIds.size} 个条目生成为最终结论？`}
          onConfirm={handleFinalize}
          okText="生成"
          cancelText="取消"
          disabled={checkedIds.size === 0}
        >
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            disabled={checkedIds.size === 0}
            loading={finalizeMutation.isPending}
          >
            {checkedIds.size > 0
              ? `生成最终结论（${checkedIds.size} 条）`
              : '生成最终结论'}
          </Button>
        </Popconfirm>
      </div>
    </div>
  );
}

export default ConclusionBar;
