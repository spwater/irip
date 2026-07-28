import { useState } from 'react';
import {
  Button,
  Input,
  message,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import type { ColumnsType } from 'antd/es/table';
import {
  apiDeleteFact,
  apiDeleteFactsByTask,
  apiListDepartments,
  apiListFacts,
  apiSearchFacts,
  apiSearchFactsByData,
  type FactSummary,
} from '@/api/client';

const { Text } = Typography;

/** 树形数据类型：父节点为任务，子节点为该任务下的 fact */
type TreeNode = {
  key: string;
  fact_id?: string;
  fact_type?: string;
  subject_id?: string;
  data_summary?: string | null;
  task_code: string | null;
  task_name: string | null;
  isGroup: boolean;
  totalCount?: number;
  department_name?: string | null;
  operator?: string | null;
  children?: TreeNode[];
};

/** 将扁平 fact 列表按 task_code 分组为树形结构 */
function groupByTask(facts: FactSummary[], groupCounts: Record<string, number>): TreeNode[] {
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
      for (const f of groupFacts) {
        tree.push({
          key: `fact-${f.fact_id}`,
          fact_id: f.fact_id,
          fact_type: f.fact_type,
          subject_id: f.subject_id,
          data_summary: f.data_summary,
          task_code: null,
          task_name: null,
          isGroup: false,
        });
      }
    } else {
      const last = groupFacts[groupFacts.length - 1];
      const children: TreeNode[] = groupFacts.map((f) => ({
        key: `fact-${f.fact_id}`,
        fact_id: f.fact_id,
        fact_type: f.fact_type,
        subject_id: f.subject_id,
        data_summary: f.data_summary,
        task_code: f.task_code,
        task_name: f.task_name,
        department_name: f.department_name,
        operator: f.operator,
        isGroup: false,
      }));
      tree.push({
        key: `task-${taskCode}`,
        task_code: taskCode,
        task_name: last.task_name,
        department_name: last.department_name,
        operator: last.operator,
        isGroup: true,
        totalCount: groupCounts[taskCode] ?? groupFacts.length,
        children,
      });
    }
  }

  return tree;
}

/**
 * 实验事实列表页面
 */
export function FactsPage(): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);

  // ---- 部门列表查询（用于筛选） ----
  const { data: deptListData } = useQuery({
    queryKey: ['departments-for-facts'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptOptions = (deptListData?.items ?? []).map((d) => ({
    value: d.display_name,
    label: d.display_name,
  }));

  // 构建部门树：选父部门时自动包含所有子部门
  const deptChildrenMap: Record<string, string[]> = {};
  for (const d of deptListData?.items ?? []) {
    if (d.parent_id) {
      const parent = deptListData?.items.find((p) => p.id === d.parent_id);
      if (parent) {
        if (!deptChildrenMap[parent.display_name]) deptChildrenMap[parent.display_name] = [];
        deptChildrenMap[parent.display_name].push(d.display_name);
      }
    }
  }

  // ---- 单条删除 Mutation ----
  const deleteMutation = useMutation({
    mutationFn: apiDeleteFact,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['facts'], exact: false });
      void queryClient.refetchQueries({ queryKey: ['facts'], exact: false });
      message.success('事实已删除');
    },
    onError: (err: unknown) => message.error(String(err)),
  });

  // ---- 批量删除 Mutation ----
  const batchDeleteMutation = useMutation({
    mutationFn: apiDeleteFactsByTask,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['facts'], exact: false });
      void queryClient.refetchQueries({ queryKey: ['facts'], exact: false });
      message.success('该任务下所有数据已删除');
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
    queryKey: ['facts', searchQuery],
    queryFn: async ({ pageParam }) => {
      if (!searchQuery) {
        return apiListFacts({ cursor: pageParam, page_size: 100 });
      }
      // 先搜元数据
      const metaResult = await apiSearchFacts({ q: searchQuery, cursor: pageParam, page_size: 100 });
      if (metaResult.items.length > 0) {
        return metaResult;
      }
      // 元数据没搜到，搜数据内容
      return apiSearchFactsByData({ q: searchQuery, page_size: 100 });
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const allItems: FactSummary[] = data?.pages.flatMap((p) => p.items) ?? [];
  const allGroupCounts: Record<string, number> = {};
  for (const page of data?.pages ?? []) {
    if (page.group_counts) {
      Object.assign(allGroupCounts, page.group_counts);
    }
  }
  const items: FactSummary[] = deptFilter
    ? allItems.filter((f) => {
        if (f.department_name === deptFilter) return true;
        const children = deptChildrenMap[deptFilter];
        return children && children.includes(f.department_name ?? '');
      })
    : allItems;
  const treeData = groupByTask(items, allGroupCounts);

  const handleSearch = (val: string): void => {
    setSearchQuery(val);
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<TreeNode> = [
    {
      title: '名称',
      key: 'name',
      width: 540,
      render: (_: unknown, record: TreeNode) => {
        if (record.isGroup) {
          return (
            <Space size={6}>
              <Text strong>{record.task_name ?? record.task_code}</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>{record.task_code}</Text>
            </Space>
          );
        }
        return <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{record.subject_id}</span>;
      },
    },
    {
      title: '所属单位',
      key: 'department_name',
      width: 160,
      render: (_: unknown, record: TreeNode) => {
        if (record.isGroup) return null;
        return record.department_name
          ? <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{record.department_name}</Tag>
          : <Text type="secondary">-</Text>;
      },
    },
    {
      title: '执行人',
      key: 'operator',
      width: 100,
      render: (_: unknown, record: TreeNode) => {
        if (record.isGroup) return null;
        return record.operator ? <Text>{record.operator}</Text> : <Text type="secondary">-</Text>;
      },
    },
    {
      title: '摘要',
      key: 'summary',
      render: (_: unknown, record: TreeNode) => {
        if (record.isGroup) return null;
        return record.data_summary
          ? <Text type="secondary" style={{ fontSize: 12 }}>{record.data_summary}</Text>
          : <Text type="secondary">-</Text>;
      },
    },
    {
      title: '事实类型',
      dataIndex: 'fact_type',
      key: 'fact_type',
      width: 130,
      render: (t: string | undefined, record: TreeNode) => {
        if (record.isGroup) return <Text type="secondary">{record.totalCount} 个样品</Text>;
        return t ? <Tag color="blue">{t}</Tag> : '-';
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_: unknown, record: TreeNode) => {
        if (record.isGroup) {
          return (
            <div onClick={(e) => e.stopPropagation()}>
            <Popconfirm
              title={`删除「${record.task_name ?? record.task_code}」下的全部数据？`}
              description={`将删除 ${record.totalCount ?? 0} 个样品，此操作不可撤销`}
              onConfirm={() => record.task_code && batchDeleteMutation.mutate(record.task_code)}
              okText="全部删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button
                type="link"
                size="small"
                danger
                loading={batchDeleteMutation.isPending}
              >
                全部删除
              </Button>
            </Popconfirm>
            </div>
          );
        }
        return (
          <div onClick={(e) => e.stopPropagation()}>
          <Popconfirm
            title="确定删除该事实？此操作不可撤销。"
            description="将同时删除所有关联数据"
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
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Input.Search
          placeholder="搜索事实..."
          allowClear
          style={{ width: 300 }}
          onSearch={handleSearch}
        />
        <Select
          placeholder="实验室筛选"
          style={{ width: 200 }}
          value={deptFilter ?? '__all__'}
          onChange={(val: string) => setDeptFilter(val === '__all__' ? undefined : val)}
          options={[{ value: '__all__', label: '全部' }, ...deptOptions]}
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
          defaultExpandAllRows: false,
          rowExpandable: (record) => record.isGroup,
        }}
        onRow={(record) => ({
          onClick: () => {
            if (!record.isGroup && record.fact_id) {
              void navigate({ to: `/facts/${record.fact_id}` });
            }
          },
          style: record.isGroup ? {} : { cursor: 'pointer' },
        })}
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
    </div>
  );
}
