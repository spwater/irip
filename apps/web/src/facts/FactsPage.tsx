import { useState } from 'react';
import {
  Button,
  Input,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import { useInfiniteQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import type { ColumnsType } from 'antd/es/table';
import {
  apiListFacts,
  apiSearchFacts,
  type FactSummary,
} from '@/api/client';
import { IngestionWizard } from '@/ingestions/IngestionWizard';

/** 状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  active: 'green',
  superseded: 'orange',
  withdrawn: 'red',
};

/** 状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  active: '活跃',
  superseded: '已替代',
  withdrawn: '已撤回',
};

const { Title } = Typography;

/**
 * 实验事实列表页面
 *
 * 功能：
 * - Ant Design Table 列表（ID / 事实类型 / 主体ID / 状态 / 质量 / 版本数 / 创建时间 / 操作）
 * - 搜索框（支持全文搜索）
 * - 状态筛选 Select
 * - 游标分页（加载更多）
 * - 点击「查看详情」跳转到事实详情页
 */
export function FactsPage(): JSX.Element {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);

  // ---- 数据查询（游标分页） ----
  const {
    data,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ['facts', statusFilter, searchQuery],
    queryFn: ({ pageParam }) =>
      searchQuery
        ? apiSearchFacts({ q: searchQuery, cursor: pageParam, page_size: 20 })
        : apiListFacts({ cursor: pageParam, page_size: 20, status: statusFilter }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const items: FactSummary[] = data?.pages.flatMap((p) => p.items) ?? [];

  // ---- 搜索处理 ----
  const handleSearch = (val: string): void => {
    setSearchQuery(val);
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<FactSummary> = [
    {
      title: 'Fact ID',
      dataIndex: 'fact_id',
      key: 'fact_id',
      width: 280,
      ellipsis: true,
      render: (id: string) => <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{id}</span>,
    },
    {
      title: '事实类型',
      dataIndex: 'fact_type',
      key: 'fact_type',
      width: 130,
      render: (t: string) => <Tag color="blue">{t}</Tag>,
    },
    {
      title: '主体ID',
      dataIndex: 'subject_id',
      key: 'subject_id',
      width: 180,
      render: (s: string) => <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{s}</span>,
    },
    {
      title: '修订号',
      dataIndex: 'revision',
      key: 'revision',
      width: 80,
      align: 'center' as const,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={STATUS_COLOR[status] ?? 'default'}>
          {STATUS_LABEL[status] ?? status}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: FactSummary) => (
        <Button
          type="link"
          size="small"
          onClick={() => void navigate({ to: `/facts/${record.fact_id}` })}
        >
          查看详情
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Title level={2}>实验事实</Title>
      <Tabs
        defaultActiveKey="list"
        items={[
          {
            key: 'list',
            label: '事实列表',
            children: (
              <>
                <Space style={{ marginBottom: 16 }}>
                  <Input.Search
                    placeholder="搜索事实..."
                    allowClear
                    style={{ width: 300 }}
                    onSearch={handleSearch}
                  />
                  <Select
                    placeholder="状态筛选"
                    allowClear
                    style={{ width: 140 }}
                    value={statusFilter}
                    onChange={(val: string | undefined) => setStatusFilter(val)}
                    options={[
                      { value: 'active', label: '活跃' },
                      { value: 'superseded', label: '已替代' },
                      { value: 'withdrawn', label: '已撤回' },
                    ]}
                  />
                </Space>

                <Table<FactSummary>
                  columns={columns}
                  dataSource={items}
                  rowKey="fact_id"
                  loading={isLoading}
                  pagination={false}
                  size="middle"
                />

                {hasNextPage && (
                  <div style={{ textAlign: 'center', marginTop: 16 }}>
                    <Button
                      loading={isFetchingNextPage}
                      onClick={() => void fetchNextPage()}
                    >
                      加载更多
                    </Button>
                  </div>
                )}
              </>
            ),
          },
          {
            key: 'ingest',
            label: '数据摄入',
            children: <IngestionWizard />,
          },
        ]}
      />
    </div>
  );
}
