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
  draft: 'blue',
  in_review: 'orange',
  published: 'green',
  deprecated: 'default',
  rejected: 'red',
};

/** 状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  in_review: '审核中',
  published: '已发布',
  deprecated: '已弃用',
  rejected: '已驳回',
};

/** 质量等级 → 颜色 */
const QUALITY_COLOR: Record<string, string> = {
  Q0: 'default',
  Q1: 'blue',
  Q2: 'gold',
  Q3: 'green',
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
        ? apiSearchFacts({ q: searchQuery, cursor: pageParam, limit: 20 })
        : apiListFacts({ cursor: pageParam, limit: 20, status: statusFilter }),
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
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 120,
    },
    {
      title: '事实类型',
      dataIndex: 'fact_type',
      key: 'fact_type',
      width: 120,
    },
    {
      title: '主体ID',
      dataIndex: 'subject_id',
      key: 'subject_id',
      width: 120,
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
      title: '质量',
      dataIndex: 'quality_level',
      key: 'quality_level',
      width: 80,
      render: (q: string) => (
        <Tag color={QUALITY_COLOR[q] ?? 'default'}>{q}</Tag>
      ),
    },
    {
      title: '版本数',
      dataIndex: 'revision_count',
      key: 'revision_count',
      width: 80,
      align: 'center' as const,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: FactSummary) => (
        <Button
          type="link"
          size="small"
          onClick={() => void navigate({ to: `/facts/${record.id}` })}
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
                      { value: 'draft', label: '草稿' },
                      { value: 'in_review', label: '审核中' },
                      { value: 'published', label: '已发布' },
                      { value: 'deprecated', label: '已弃用' },
                      { value: 'rejected', label: '已驳回' },
                    ]}
                  />
                </Space>

                <Table<FactSummary>
                  columns={columns}
                  dataSource={items}
                  rowKey="id"
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
