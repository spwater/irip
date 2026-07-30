import { useMemo, useState } from 'react';
import {
  Button,
  DatePicker,
  Input,
  message,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import type { Dayjs } from 'dayjs';
import {
  apiCreateAuditExport,
  apiListAuditEvents,
  type AuditEventItem,
} from '@/api/governance';
import { extractApiError } from '@/api/types';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { DataTableShell } from '@/shared/ui';
import { QueryStateDisplay } from '@/features/components/StateDisplay';

const { Text } = Typography;
const { RangePicker } = DatePicker;

/** 已应用的筛选条件（提交查询后固化，用于构造请求参数） */
interface AppliedFilters {
  object_type: string | undefined;
  object_id: string | undefined;
  user_id: string | undefined;
  action: string | undefined;
  start_date: string | undefined;
  end_date: string | undefined;
}

/** 空的已应用筛选条件（用于初始化和重置） */
const EMPTY_FILTERS: AppliedFilters = {
  object_type: undefined,
  object_id: undefined,
  user_id: undefined,
  action: undefined,
  start_date: undefined,
  end_date: undefined,
};

/**
 * 审计事件页面
 *
 * 功能：
 * - 筛选条件（对象类型 / 对象 ID / 用户 / 操作 / 日期范围）
 * - Table: 审计事件列表（游标分页）
 * - 导出按钮（异步作业）
 *
 * M-07 整改：
 * - draft / applied filters 分离：输入时不请求，点击「查询」才提交
 * - 查询时原子清 cursor 和累积结果，避免新旧筛选数据混用
 * - 条件切换不拼旧数据
 */
export function AuditPage(): JSX.Element {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);

  // 审计读取权限检查
  const canRead: boolean = user?.permissions?.includes('audit:read') ?? false;

  // ---- Draft 筛选状态（仅输入，不触发请求）----
  const [draftObjectType, setDraftObjectType] = useState<string | undefined>(undefined);
  const [draftObjectId, setDraftObjectId] = useState<string | undefined>(undefined);
  const [draftUserId, setDraftUserId] = useState<string | undefined>(undefined);
  const [draftAction, setDraftAction] = useState<string | undefined>(undefined);
  const [draftDateRange, setDraftDateRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);

  // ---- Applied 筛选状态（提交查询后固化，用于构造请求参数）----
  const [appliedFilters, setAppliedFilters] = useState<AppliedFilters>(EMPTY_FILTERS);

  // 累积已加载的所有项（用于游标分页的"加载更多"模式）
  const [allItems, setAllItems] = useState<AuditEventItem[]>([]);
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);

  // ---- 构建查询参数（仅依赖 applied filters + cursor，不依赖 draft）----
  const queryParams = useMemo(
    () => ({
      object_type: appliedFilters.object_type || undefined,
      object_id: appliedFilters.object_id || undefined,
      user_id: appliedFilters.user_id || undefined,
      action: appliedFilters.action || undefined,
      start_date: appliedFilters.start_date || undefined,
      end_date: appliedFilters.end_date || undefined,
      cursor: currentCursor ?? undefined,
      limit: 50,
    }),
    [appliedFilters, currentCursor],
  );

  // ---- 数据查询 ----
  // queryKey 包含 applied filters 和 cursor；draft 变化不触发请求
  const { data, isLoading, isError, error, isFetching, refetch } = useQuery({
    queryKey: ['audit-events', queryParams],
    queryFn: () => apiListAuditEvents(queryParams),
    enabled: canRead,
  });

  // 当 data 变化时更新累积列表
  const pageItems: AuditEventItem[] = data?.items ?? [];
  const hasNext: boolean = data?.has_more ?? false;
  const nextCursor: string | null = data?.next_cursor ?? null;

  // 合并结果：cursor 有值时追加，无值时替换（筛选条件变化后重置）
  const displayItems: AuditEventItem[] =
    currentCursor === null ? pageItems : [...allItems, ...pageItems];

  // ---- 导出 Mutation ----
  const exportMutation = useMutation({
    mutationFn: () =>
      apiCreateAuditExport({
        object_type: draftObjectType || null,
        object_id: draftObjectId || null,
        user_id: draftUserId || null,
        action: draftAction || null,
        start_date: draftDateRange?.[0]?.toISOString() ?? null,
        end_date: draftDateRange?.[1]?.toISOString() ?? null,
        format: 'csv',
      }),
    onSuccess: (result) => {
      message.success(`导出作业已创建（ID: ${result.job_id}），请在作业中心查看进度`);
      void queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 事件处理 ----

  /**
   * 提交查询：将 draft 固化为 applied，原子清 cursor 和累积结果。
   * 这样新筛选条件不会与旧筛选的累积数据混用。
   */
  const handleSearch = (): void => {
    setAppliedFilters({
      object_type: draftObjectType,
      object_id: draftObjectId,
      user_id: draftUserId,
      action: draftAction,
      start_date: draftDateRange?.[0]?.toISOString() ?? undefined,
      end_date: draftDateRange?.[1]?.toISOString() ?? undefined,
    });
    setAllItems([]);
    setCurrentCursor(null);
  };

  const handleLoadMore = (): void => {
    if (nextCursor) {
      setAllItems(displayItems);
      setCurrentCursor(nextCursor);
    }
  };

  /**
   * 重置：清空 draft 和 applied，原子清 cursor 和累积结果。
   */
  const handleReset = (): void => {
    setDraftObjectType(undefined);
    setDraftObjectId(undefined);
    setDraftUserId(undefined);
    setDraftAction(undefined);
    setDraftDateRange(null);
    setAppliedFilters(EMPTY_FILTERS);
    setAllItems([]);
    setCurrentCursor(null);
  };

  // ---- 权限检查 ----
  if (!canRead) {
    return (
      <div>
        <Text type="danger">您没有审计读取权限。</Text>
      </div>
    );
  }

  // ---- 表格列定义 ----
  const columns: ColumnsType<AuditEventItem> = [
    {
      title: '发生时间',
      dataIndex: 'occurred_at',
      key: 'occurred_at',
      width: 180,
      render: (val: string) => new Date(val).toLocaleString(),
    },
    {
      title: '操作者',
      dataIndex: 'actor_user_id',
      key: 'actor_user_id',
      width: 280,
      ellipsis: true,
      render: (val: string | null) =>
        val ? (
          <Tooltip title={val}>
            <Text style={{ fontSize: 12 }}>{val.slice(0, 12)}...</Text>
          </Tooltip>
        ) : (
          <Text type="secondary">系统</Text>
        ),
    },
    {
      title: '动作',
      dataIndex: 'action',
      key: 'action',
      width: 200,
      render: (val: string) => <Tag color="blue">{val}</Tag>,
    },
    {
      title: '资源类型',
      dataIndex: 'resource_type',
      key: 'resource_type',
      width: 140,
      render: (val: string | null) => val ?? <Text type="secondary">-</Text>,
    },
    {
      title: '资源 ID',
      dataIndex: 'resource_id',
      key: 'resource_id',
      width: 280,
      ellipsis: true,
      render: (val: string | null) =>
        val ? (
          <Tooltip title={val}>
            <Text style={{ fontSize: 12 }}>{val.slice(0, 12)}...</Text>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: 'IP',
      dataIndex: 'ip',
      key: 'ip',
      width: 140,
      render: (val: string | null) => val ?? <Text type="secondary">-</Text>,
    },
    {
      title: '载荷',
      dataIndex: 'payload',
      key: 'payload',
      width: 200,
      ellipsis: true,
      render: (val: Record<string, unknown> | null) =>
        val ? (
          <Tooltip title={JSON.stringify(val, null, 2)}>
            <Text style={{ fontSize: 12 }}>{JSON.stringify(val).slice(0, 40)}...</Text>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
  ];

  return (
    <div>
      {/* 筛选区 */}
      <div style={{ marginBottom: 16 }}>
        <Space wrap style={{ marginBottom: 8 }}>
          <Select
            placeholder="对象类型"
            style={{ width: 160 }}
            value={draftObjectType ?? '__all__'}
            onChange={(val: string) => setDraftObjectType(val === '__all__' ? undefined : val)}
            options={[
              { value: '__all__', label: '全部' },
              { value: 'app_user', label: '用户' },
              { value: 'scope_grant', label: '授权' },
              { value: 'fact', label: '事实' },
              { value: 'artifact', label: '工件' },
              { value: 'job', label: '作业' },
              { value: 'variable', label: '变量' },
              { value: 'object', label: '工业对象' },
              { value: 'parameter', label: '参数' },
            ]}
          />
          <Input
            placeholder="对象 ID"
            allowClear
            style={{ width: 280 }}
            value={draftObjectId}
            onChange={(e) => setDraftObjectId(e.target.value || undefined)}
          />
          <Input
            placeholder="操作者 ID"
            allowClear
            style={{ width: 280 }}
            value={draftUserId}
            onChange={(e) => setDraftUserId(e.target.value || undefined)}
          />
        </Space>
        <Space wrap>
          <Input
            placeholder="动作（如 governance.user.assign_roles）"
            allowClear
            style={{ width: 320 }}
            value={draftAction}
            onChange={(e) => setDraftAction(e.target.value || undefined)}
          />
          <RangePicker
            showTime
            value={draftDateRange}
            onChange={(range) => {
              if (range) {
                setDraftDateRange([range[0], range[1]]);
              } else {
                setDraftDateRange(null);
              }
            }}
          />
          <Button type="primary" onClick={handleSearch} loading={isFetching}>
            查询
          </Button>
          <Button onClick={handleReset}>重置</Button>
          <Button
            onClick={() => exportMutation.mutate()}
            loading={exportMutation.isPending}
          >
            导出（异步作业）
          </Button>
        </Space>
      </div>

      <DataTableShell
        bodyPadding={0}
        footer={
          hasNext ? (
            <div style={{ textAlign: 'center' }}>
              <Button type="link" onClick={handleLoadMore} loading={isFetching}>
                加载更多
              </Button>
            </div>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              共 {displayItems.length} 条记录
            </Text>
          )
        }
      >
        <QueryStateDisplay
          isLoading={isLoading}
          isError={isError}
          error={error}
          isEmpty={!isLoading && !isError && displayItems.length === 0}
          emptyText="暂无审计记录"
          onRetry={() => void refetch()}
          loadingTitle="加载审计事件…"
        >
          <Table<AuditEventItem>
            columns={columns}
            dataSource={displayItems}
            rowKey="id"
            pagination={false}
            size="middle"
          />
        </QueryStateDisplay>
      </DataTableShell>
    </div>
  );
}
