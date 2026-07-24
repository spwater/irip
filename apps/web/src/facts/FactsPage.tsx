import { useState } from 'react';
import {
  Button,
  Input,
  message,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import type { ColumnsType } from 'antd/es/table';
import {
  apiDeleteFact,
  apiListFacts,
  apiSearchFacts,
  type FactSummary,
} from '@/api/client';
import { IngestionWizard } from '@/ingestions/IngestionWizard';

const { Text } = Typography;

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

/** 树形数据类型：父节点为任务，子节点为该任务下的 fact */
type TreeNode = {
  key: string;
  fact_id?: string;
  revision?: number;
  fact_type?: string;
  subject_id?: string;
  status?: string;
  task_code: string | null;
  task_name: string | null;
  isGroup: boolean;
  childCount?: number;
  children?: TreeNode[];
};

/** 将扁平 fact 列表按 task_code 分组为树形结构 */
function groupByTask(facts: FactSummary[]): TreeNode[] {
  const groups = new Map<string, FactSummary[]>();
  const noTaskKey = '__no_task__';

  for (const f of facts) {
    const key = f.task_code ?? noTaskKey;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(f);
  }

  const tree: TreeNode[] = [];

  for (const [taskCode, groupFacts] of groups) {
    if (taskCode === noTaskKey) {
      // 没有关联任务的 fact 直接作为叶子节点
      for (const f of groupFacts) {
        tree.push({
          key: `fact-${f.fact_id}`,
          fact_id: f.fact_id,
          revision: f.revision,
          fact_type: f.fact_type,
          subject_id: f.subject_id,
          status: f.status,
          task_code: null,
          task_name: null,
          isGroup: false,
        });
      }
    } else {
      // 有关联任务的 fact 收拢为子节点
      const first = groupFacts[0];
      const children: TreeNode[] = groupFacts.map((f) => ({
        key: `fact-${f.fact_id}`,
        fact_id: f.fact_id,
        revision: f.revision,
        fact_type: f.fact_type,
        subject_id: f.subject_id,
        status: f.status,
        task_code: f.task_code,
        task_name: f.task_name,
        isGroup: false,
      }));
      tree.push({
        key: `task-${taskCode}`,
        task_code: taskCode,
        task_name: first.task_name,
        isGroup: true,
        childCount: groupFacts.length,
        children,
      });
    }
  }

  return tree;
}

/**
 * 实验事实列表页面
 *
 * 功能：
 * - 树形表格：按任务编码分组，同任务下的 fact 收拢为子节点
 * - 搜索框（支持全文搜索）
 * - 状态筛选 Select
 * - 游标分页（加载更多）
 * - 点击「查看详情」跳转到事实详情页
 */
export function FactsPage(): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);

  // ---- 删除 Mutation ----
  const deleteMutation = useMutation({
    mutationFn: apiDeleteFact,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['facts'], exact: false });
      void queryClient.refetchQueries({ queryKey: ['facts'], exact: false });
      message.success('事实已删除');
    },
    onError: (err: unknown) => message.error(String(err)),
  });

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
  const treeData = groupByTask(items);

  // ---- 搜索处理 ----
  const handleSearch = (val: string): void => {
    setSearchQuery(val);
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<TreeNode> = [
    {
      title: '名称 / 主体ID',
      key: 'name',
      render: (_: unknown, record: TreeNode) => {
        if (record.isGroup) {
          return (
            <div>
              <Text strong>{record.task_name ?? record.task_code}</Text>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>{record.task_code}</Text>
              </div>
            </div>
          );
        }
        return <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{record.subject_id}</span>;
      },
    },
    {
      title: '事实类型',
      dataIndex: 'fact_type',
      key: 'fact_type',
      width: 130,
      render: (t: string | undefined, record: TreeNode) => {
        if (record.isGroup) return <Text type="secondary">{record.childCount} 条数据</Text>;
        return t ? <Tag color="blue">{t}</Tag> : '-';
      },
    },
    {
      title: '修订号',
      dataIndex: 'revision',
      key: 'revision',
      width: 80,
      align: 'center' as const,
      render: (r: number | undefined, record: TreeNode) => {
        if (record.isGroup) return '-';
        return r;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string | undefined, record: TreeNode) => {
        if (record.isGroup) return '-';
        return status ? (
          <Tag color={STATUS_COLOR[status] ?? 'default'}>
            {STATUS_LABEL[status] ?? status}
          </Tag>
        ) : '-';
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: unknown, record: TreeNode) => {
        if (record.isGroup) return null;
        return (
          <Space size="small">
            <Button
              type="link"
              size="small"
              onClick={() => record.fact_id && void navigate({ to: `/facts/${record.fact_id}` })}
            >
              查看详情
            </Button>
            <Popconfirm
              title="确定删除该事实？此操作不可撤销。"
              description="将同时删除所有修订、观察值和关联数据"
              onConfirm={() => record.fact_id && deleteMutation.mutate(record.fact_id)}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button
                type="link"
                size="small"
                danger
                loading={deleteMutation.isPending}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <div>
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
                    style={{ width: 140 }}
                    value={statusFilter ?? '__all__'}
                    onChange={(val: string) => setStatusFilter(val === '__all__' ? undefined : val)}
                    options={[
                      { value: '__all__', label: '全部' },
                      { value: 'active', label: '活跃' },
                      { value: 'superseded', label: '已替代' },
                      { value: 'withdrawn', label: '已撤回' },
                    ]}
                  />
                </Space>

                <Table<TreeNode>
                  columns={columns}
                  dataSource={treeData}
                  rowKey="key"
                  loading={isLoading}
                  pagination={false}
                  size="middle"
                  expandable={{
                    defaultExpandAllRows: true,
                    rowExpandable: (record) => record.isGroup,
                  }}
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
