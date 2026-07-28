import { useState } from 'react';
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
  extractApiError,
  type AuditEventItem,
} from '@/api/client';
import { useAuthStore } from '@/auth/AuthProvider';
import { ActionBar, DataTableShell } from '@/components/ui';

const { Text } = Typography;
const { RangePicker } = DatePicker;

/**
 * 审计事件页面
 *
 * 功能：
 * - 筛选条件（对象类型 / 对象 ID / 用户 / 操作 / 日期范围）
 * - Table: 审计事件列表（游标分页）
 * - 导出按钮（异步作业）
 */
export function AuditPage(): JSX.Element {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);

  // 审计读取权限检查
  const canRead: boolean = user?.permissions?.includes('audit:read') ?? false;

  // ---- 筛选状态 ----
  const [objectType, setObjectType] = useState<string | undefined>(undefined);
  const [objectId, setObjectId] = useState<string | undefined>(undefined);
  const [userId, setUserId] = useState<string | undefined>(undefined);
  const [action, setAction] = useState<string | undefined>(undefined);
  const [dateRange, setDateRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);

  // 累积已加载的所有项（用于游标分页的"加载更多"模式）
  const [allItems, setAllItems] = useState<AuditEventItem[]>([]);
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);

  // ---- 构建查询参数 ----
  const queryParams = {
    object_type: objectType || undefined,
    object_id: objectId || undefined,
    user_id: userId || undefined,
    action: action || undefined,
    start_date: dateRange?.[0]?.toISOString() ?? undefined,
    end_date: dateRange?.[1]?.toISOString() ?? undefined,
    cursor: currentCursor ?? undefined,
    limit: 50,
  };

  // ---- 数据查询 ----
  const { data, isLoading, isFetching } = useQuery({
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
        object_type: objectType || null,
        object_id: objectId || null,
        user_id: userId || null,
        action: action || null,
        start_date: dateRange?.[0]?.toISOString() ?? null,
        end_date: dateRange?.[1]?.toISOString() ?? null,
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

  const handleSearch = (): void => {
    setAllItems([]);
    setCurrentCursor(null);
  };

  const handleLoadMore = (): void => {
    if (nextCursor) {
      setAllItems(displayItems);
      setCurrentCursor(nextCursor);
    }
  };

  const handleReset = (): void => {
    setObjectType(undefined);
    setObjectId(undefined);
    setUserId(undefined);
    setAction(undefined);
    setDateRange(null);
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
            <Text style={{ fontSize: 12 }}>{val.slice(0, 12)}…</Text>
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
            <Text style={{ fontSize: 12 }}>{val.slice(0, 12)}…</Text>
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
            <Text style={{ fontSize: 12 }}>{JSON.stringify(val).slice(0, 40)}…</Text>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
  ];

  return (
    <div>
      {/* 筛选区 */}
      <ActionBar style={{ marginBottom: 16, flexDirection: 'column', alignItems: 'flex-start' }}>
        <Space wrap>
          <Select
            placeholder="对象类型"
            style={{ width: 160 }}
            value={objectType ?? '__all__'}
            onChange={(val: string) => setObjectType(val === '__all__' ? undefined : val)}
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
            value={objectId}
            onChange={(e) => setObjectId(e.target.value || undefined)}
          />
          <Input
            placeholder="操作者 ID"
            allowClear
            style={{ width: 280 }}
            value={userId}
            onChange={(e) => setUserId(e.target.value || undefined)}
          />
        </Space>
        <Space wrap>
          <Input
            placeholder="动作（如 governance.user.assign_roles）"
            allowClear
            style={{ width: 320 }}
            value={action}
            onChange={(e) => setAction(e.target.value || undefined)}
          />
          <RangePicker
            showTime
            value={dateRange}
            onChange={(range) => {
              if (range) {
                setDateRange([range[0], range[1]]);
              } else {
                setDateRange(null);
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
      </ActionBar>

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
        <Table<AuditEventItem>
          columns={columns}
          dataSource={displayItems}
          rowKey="id"
          loading={isLoading}
          pagination={false}
          size="middle"
        />
      </DataTableShell>
    </div>
  );
}
