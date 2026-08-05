/**
 * ObjectTypesList — 类型管理列表子组件。
 *
 * 从 ExperimentalObjectPage.tsx 提取。独立查询类型字典并渲染表格，
 * 通过 props 回调通知父组件编辑/删除操作。
 */

import { Button, Popconfirm, Space, Spin, Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { apiListObjectTypes, type ObjectTypeDictItem } from '@/api/standards-objects';

const { Text } = Typography;

export interface ObjectTypesListProps {
  onEdit: (item: ObjectTypeDictItem) => void;
  onDelete: (item: ObjectTypeDictItem) => void;
}

export function ObjectTypesList(props: ObjectTypesListProps): JSX.Element {
  const { onEdit, onDelete } = props;
  const { data, isLoading } = useQuery({
    queryKey: ['object-types'],
    queryFn: apiListObjectTypes,
  });
  const items = data ?? [];
  if (isLoading) return <Spin />;
  if (items.length === 0) return <Text type="secondary">暂无类型</Text>;
  return (
    <Table<ObjectTypeDictItem>
      dataSource={items}
      rowKey="id"
      size="small"
      pagination={false}
      columns={[
        { title: '名称', dataIndex: 'display_name', key: 'display_name', width: 120 },
        { title: '编码', dataIndex: 'code', key: 'code', width: 140 },
        { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
        {
          title: '操作',
          key: 'action',
          width: 120,
          render: (_: unknown, record: ObjectTypeDictItem) => (
            <Space size="small">
              <Button type="link" size="small" onClick={() => onEdit(record)}>
                改名
              </Button>
              <Popconfirm
                title="确定删除该类型？"
                description="如果类型下有对象则无法删除"
                onConfirm={() => onDelete(record)}
                okText="确定"
                cancelText="取消"
              >
                <Button type="link" size="small" danger>
                  删除
                </Button>
              </Popconfirm>
            </Space>
          ),
        },
      ]}
    />
  );
}
