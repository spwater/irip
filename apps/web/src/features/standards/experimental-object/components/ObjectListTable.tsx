/**
 * ObjectListTable — 实验对象列表 Table（树形）。
 *
 * 从 ExperimentalObjectPage.tsx 提取。包含列定义和表格渲染。
 * 通过 props 传递数据和回调，不共享 state。
 */

import { Button, Popconfirm, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { IndustrialObject } from '@/api/types';
import { STATUS_COLOR, STATUS_LABEL, type TreeRow } from '../types';
import { isTypeRow } from '../utils/buildTreeData';

const { Text } = Typography;

export interface ObjectListTableProps {
  treeData: TreeRow[];
  loading: boolean;
  deptMap: Map<string, string>;
  componentMap: Map<string, string>;
  onEdit: (record: IndustrialObject) => void;
  onToggleStatus: (record: IndustrialObject) => void;
}

export function ObjectListTable(props: ObjectListTableProps): JSX.Element {
  const { treeData, loading, deptMap, componentMap, onEdit, onToggleStatus } =
    props;

  const columns: ColumnsType<TreeRow> = [
    {
      title: '名称',
      key: 'name',
      width: 333,
      render: (_: unknown, record: TreeRow) => {
        if (isTypeRow(record)) {
          return (
            <Space size={6}>
              <Text strong style={{ fontSize: 14 }}>{record.display_name}</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {record.code}
              </Text>
            </Space>
          );
        }
        return (
          <Tooltip title={record.description || undefined} placement="topLeft">
            <Space size={6}>
              <Text strong>{record.display_name}</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {record.code}
              </Text>
            </Space>
          </Tooltip>
        );
      },
    },
    {
      title: '所属单位',
      dataIndex: 'department_id',
      key: 'department_id',
      width: 140,
      render: (deptId: string | null, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        const name = deptId ? deptMap.get(deptId) : null;
        return name ? <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{name}</Tag> : <Text type="secondary">-</Text>;
      },
    },
    {
      title: '可见单位',
      dataIndex: 'visible_departments',
      key: 'visible_departments',
      width: 300,
      render: (deptIds: string[] | null, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        if (!deptIds || deptIds.length === 0) {
          return <Text type="secondary">-</Text>;
        }
        return (
          <Space size="small" wrap>
            {deptIds.map((id) => {
              const name = deptMap.get(id);
              return name ? <Tag key={id} color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{name}</Tag> : null;
            })}
          </Space>
        );
      },
    },
    {
      title: '数据接口',
      dataIndex: 'component_id',
      key: 'component_id',
      width: 150,
      render: (compId: string | null, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        if (!compId) return <Text type="secondary">-</Text>;
        const comp = componentMap.get(compId);
        return comp ? <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{comp}</Tag> : <Text type="secondary">-</Text>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (s: string, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        return (
          <Tag color={STATUS_COLOR[s] ?? 'default'}>
            {STATUS_LABEL[s] ?? s}
          </Tag>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: unknown, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        return (
          <Space size="small">
            <Button
              type="link"
              size="small"
              onClick={() => onEdit(record)}
            >
              编辑
            </Button>
            <Popconfirm
              title={
                record.status === 'active'
                  ? '确定禁用该对象？'
                  : '确定启用该对象？'
              }
              onConfirm={() => onToggleStatus(record)}
              okText="确定"
              cancelText="取消"
            >
              <Button
                type="link"
                size="small"
                danger={record.status === 'active'}
              >
                {record.status === 'active' ? '禁用' : '启用'}
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <Table<TreeRow>
      columns={columns}
      dataSource={treeData}
      rowKey="id"
      loading={loading}
      pagination={false}
      size="middle"
      expandable={{ defaultExpandAllRows: false }}
    />
  );
}
