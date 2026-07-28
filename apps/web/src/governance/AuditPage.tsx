import { useState } from 'react';
import {
  Button,
  DatePicker,
  Input,
  message,
  Select,
  Space,
  Table,
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
import { ActionBar, DataTableShell, StatusMark, FeedbackState, OceanPanel } from '@/components/ui';

const { Text } = Typography;
const { RangePicker } = DatePicker;

/**
 * 审计事件页面
 *
 * 功能：
 * - 筛选条件（对象类型 / 对象 ID / 用户 / 操作 / 日期范围）
 * - Table: 审计事件列表（游标分页）— 稳定强表面 + 紧凑行密度
 * - 技术ID 等宽显示 + tabular 时间戳 + 显式结果状态
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
  const { data, isLoading, isFetching, isError, refetch } = useQuery({
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
      <FeedbackState
        kind="forbidden"
        title="您没有审计读取权限。"
      />
    );
  }

  // ---- 表格列定义 ----
  // 紧凑行密度 + tabular 时间戳 + 技术ID 等宽 + 显式结果状态
  const columns: ColumnsType<AuditEventItem> = [
    {
      title: '发生时间',
      dataIndex: 'occurred_at',
      key: 'occurred_at',
      width: 180,
      render: (val: string) => (
        <span className="ocean-tabular-number" style={{ fontSize: 12 }}>
          {new Date(val).toLocaleString('zh-CN', { hour12: false })}
        </span>
      ),
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
            <span className="ocean-tech" style={{ fontSize: 12 }}>{val.slice(0, 16)}…</span>
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
      render: (val: string) => <span className="ocean-tech" style={{ fontSize: 12 }}>{val}</span>,
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
            <span className="ocean-tech" style={{ fontSize: 12 }}>{val.slice(0, 16)}…</span>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '结果',
      key: 'result',
      width: 100,
      render: (_: unknown, record: AuditEventItem) => {
        // 从 payload 中提取结果状态（如 success/error）
        const payload = record.payload;
        if (payload && typeof payload === 'object' && 'result' in payload) {
          const resultVal = String((payload as Record<string, unknown>).result);
          if (resultVal === 'success' || resultVal === 'ok') {
            return <StatusMark tone="success" label="成功" />;
          }
          if (resultVal === 'error' || resultVal === 'failed') {
            return <StatusMark tone="danger" label="失败" />;
          }
          return <StatusMark tone="neutral" label={resultVal} />;
        }
        // 无 payload 中的结果字段时，默认标记为已记录
        return <StatusMark tone="neutral" label="已记录" />;
      },
    },
    {
      title: 'IP',
      dataIndex: 'ip',
      key: 'ip',
      width: 140,
      render: (val: string | null) =>
        val ? <span className="ocean-tech" style={{ fontSize: 12 }}>{val}</span> : <Text type="secondary">-</Text>,
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
            <span className="ocean-tech" style={{ fontSize: 12 }}>{JSON.stringify(val).slice(0, 40)}…</span>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
  ];

  // ---- 工具栏 ----
  const toolbar = (
    <ActionBar
      filters={
        <>
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
        </>
      }
      actions={
        <>
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
        </>
      }
    />
  );

  // ---- 表格内容 ----
  const tableContent: JSX.Element = (() => {
    if (isLoading && displayItems.length === 0) {
      return <FeedbackState kind="loading" title="正在加载审计事件..." rows={6} />;
    }
    if (isError && displayItems.length === 0) {
      return (
        <FeedbackState
          kind="error"
          title="审计事件加载失败"
          onRetry={() => void refetch()}
        />
      );
    }
    return (
      <>
        {isError && displayItems.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <FeedbackState
              kind="partial"
              title="部分数据加载失败"
              description="已显示已加载的记录，可点击重试获取完整数据。"
              onRetry={() => void refetch()}
            />
          </div>
        )}
        <Table<AuditEventItem>
          columns={columns}
          dataSource={displayItems}
          rowKey="id"
          loading={isLoading}
          pagination={false}
          size="small"
          footer={() =>
            hasNext ? (
              <div style={{ textAlign: 'center' }}>
                <Button type="link" onClick={handleLoadMore} loading={isFetching}>
                  加载更多
                </Button>
              </div>
            ) : (
              <Text type="secondary" style={{ fontSize: 12 }}>
                共 <span className="ocean-tabular-number">{displayItems.length}</span> 条记录
              </Text>
            )
          }
        />
      </>
    );
  })();

  return (
    <section aria-label="审计事件目录">
      <DataTableShell
        title="审计事件"
        description="平台操作审计记录，支持筛选和异步导出。"
        toolbar={toolbar}
      >
        <OceanPanel level="strong">
          {tableContent}
        </OceanPanel>
      </DataTableShell>
    </section>
  );
}
