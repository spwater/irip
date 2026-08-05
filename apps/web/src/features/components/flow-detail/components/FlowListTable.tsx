/**
 * FlowListTable — 流程列表 Table。
 *
 * 从 FlowDetail.tsx 提取。包含 flowColumns 列定义和表格渲染。
 */

import { Button, Card, Popconfirm, Select, Space, Table, Tag, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { FlowSummary } from '@/api/equipment-flows';
import { PrivateBadge } from '@/shared/PrivateBadge';
import { fmtTime, STATUS_COLOR, STATUS_LABEL } from '../../shared';
import type { CanManageFn } from '../types';

const { Text } = Typography;

export interface FlowListTableProps {
  flows: FlowSummary[];
  loading: boolean;
  flowPageSize: number;
  setFlowPageSize: (size: number) => void;
  onSelectFlow: (id: string) => void;
  showArchived: boolean;
  setShowArchived: (val: boolean) => void;
  projectStatus?: string;
  onCreateFlow: () => void;
  deptFilter: string | undefined;
  setDeptFilter: (val: string | undefined) => void;
  equipFilter: string | undefined;
  setEquipFilter: (val: string | undefined) => void;
  deptOptions: { value: string; label: string }[];
  equipOptions: { value: string; label: string }[];
  objMap: Map<string, { display_name: string }>;
  deptMap: Map<string, string>;
  canManage: CanManageFn;
  onEdit: (record: FlowSummary) => void;
  onArchive: (id: string) => void;
  onRestore: (id: string) => void;
  onDelete: (id: string) => void;
  onMakePublic: (id: string) => Promise<void>;
  archivePending: boolean;
  restorePending: boolean;
  deletePending: boolean;
}

export function FlowListTable(props: FlowListTableProps): JSX.Element {
  const {
    flows,
    loading,
    flowPageSize,
    setFlowPageSize,
    onSelectFlow,
    showArchived,
    setShowArchived,
    projectStatus,
    onCreateFlow,
    deptFilter,
    setDeptFilter,
    equipFilter,
    setEquipFilter,
    deptOptions,
    equipOptions,
    objMap,
    deptMap,
    canManage,
    onEdit,
    onArchive,
    onRestore,
    onDelete,
    onMakePublic,
    archivePending,
    restorePending,
    deletePending,
  } = props;

  const flowColumns: ColumnsType<FlowSummary> = [
    {
      title: '任务名称',
      key: 'name',
      width: 200,
      render: (_: unknown, record: FlowSummary) => (
        <div>
          <Text strong>{record.display_name || record.code}</Text>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {record.code}
            </Text>
          </div>
        </div>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      sorter: (a: FlowSummary, b: FlowSummary) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      defaultSortOrder: 'ascend',
      render: (v: string) => fmtTime(v),
    },
    {
      title: '执行人',
      dataIndex: 'operator',
      key: 'operator',
      width: 100,
      render: (v: string | null) => v ?? <Text type="secondary">-</Text>,
    },
    {
      title: '实验对象',
      dataIndex: 'experimental_object_code',
      key: 'experimental_object_code',
      width: 120,
      render: (code: string | null) => {
        if (!code) return <Text type="secondary">-</Text>;
        const obj = objMap.get(code);
        return (
          <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
            {obj?.display_name ?? code}
          </Tag>
        );
      },
    },
    {
      title: '任务来源',
      key: 'department',
      width: 300,
      render: (_: unknown, record: FlowSummary) => {
        const deptName = record.department_id ? deptMap.get(record.department_id) : null;
        if (!deptName) return <Text type="secondary">-</Text>;
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {deptName && (
              <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                {deptName}
              </Tag>
            )}
            {(record as Record<string, unknown>).visibility_scope === 'private' && (
              <PrivateBadge visibility_scope="private" />
            )}
          </div>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] ?? 'default'}>{STATUS_LABEL[v] ?? v}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_: unknown, record: FlowSummary) =>
        record.status === 'deprecated' ? (
          <Space size="small">
            <Popconfirm
              title="确定恢复该流程？"
              onConfirm={(e) => { e?.stopPropagation(); onRestore(record.id); }}
              okText="恢复"
              cancelText="取消"
            >
              <Button type="link" size="small" onClick={(e) => e.stopPropagation()} loading={restorePending}>
                恢复
              </Button>
            </Popconfirm>
            <Popconfirm
              title="确定删除该流程？"
              description="将同时删除其所有版本和运行记录，不可撤销"
              onConfirm={(e) => { e?.stopPropagation(); onDelete(record.id); }}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              disabled={!canManage(record)}
            >
              <Button type="link" size="small" danger onClick={(e) => e.stopPropagation()} loading={deletePending}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <Space size="small">
            <Button
              type="link"
              size="small"
              disabled={!canManage(record)}
              onClick={(e) => { e.stopPropagation(); onEdit(record); }}
            >
              编辑
            </Button>
            {(record as Record<string, unknown>).visibility_scope === 'private' && (
              <Popconfirm
                title="确认公开此流程？"
                description="此操作【不可逆】，公开后部门内所有成员可见。"
                onConfirm={async (e) => { e?.stopPropagation(); await onMakePublic(record.id); }}
                okText="确认公开"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button type="link" size="small" danger onClick={(e) => e.stopPropagation()}>
                  公开
                </Button>
              </Popconfirm>
            )}
            <Popconfirm
              title="确定归档该流程？"
              onConfirm={(e) => { e?.stopPropagation(); onArchive(record.id); }}
              okText="归档"
              cancelText="取消"
              disabled={!canManage(record)}
            >
              <Button type="link" size="small" danger onClick={(e) => e.stopPropagation()} loading={archivePending}>
                归档
              </Button>
            </Popconfirm>
          </Space>
        ),
    },
  ];

  return (
    <>
      <Space style={{ marginBottom: 16, alignItems: 'center' }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={onCreateFlow}
          disabled={projectStatus === 'archived'}
        >
          新建任务
        </Button>
        <Button
          type={showArchived ? 'default' : 'primary'}
          onClick={() => setShowArchived(false)}
        >
          活跃
        </Button>
        <Button
          type={showArchived ? 'primary' : 'default'}
          onClick={() => setShowArchived(true)}
        >
          归档
        </Button>
        <Select
          placeholder="所属单位筛选"
          style={{ width: 200 }}
          value={deptFilter ?? '__all__'}
          onChange={(val: string) => setDeptFilter(val === '__all__' ? undefined : val)}
          options={[{ value: '__all__', label: '全部' }, ...deptOptions]}
        />
        <Select
          placeholder="实验设备筛选"
          style={{ width: 200 }}
          value={equipFilter ?? '__all__'}
          onChange={(val: string) => setEquipFilter(val === '__all__' ? undefined : val)}
          options={[{ value: '__all__', label: '全部' }, ...equipOptions]}
        />
      </Space>

      <Card title="任务列表" style={{ marginBottom: 16 }}>
        <Table<FlowSummary>
          columns={flowColumns}
          dataSource={flows}
          rowKey="id"
          loading={loading}
          pagination={{
            pageSize: flowPageSize,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50],
            onShowSizeChange: (_: number, size: number) => setFlowPageSize(size),
          }}
          size="middle"
          onRow={(record) => ({
            onClick: () => onSelectFlow(record.id),
            style: { cursor: 'pointer' },
          })}
        />
      </Card>
    </>
  );
}
