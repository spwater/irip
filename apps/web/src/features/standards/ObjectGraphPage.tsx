import { useState } from 'react';
import {
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tree,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import type { DataNode } from 'antd/es/tree';
import {
  apiCreateObject,
  apiGetObjectDescendants,
  apiListObjectRelations,
  apiListObjects,
} from '@/api/standards-objects';
import { extractApiError, type IndustrialObject, type ObjectRelation } from '@/api/types';

const { Title, Text } = Typography;

/** 状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  active: 'green',
  inactive: 'default',
  draft: 'blue',
  published: 'green',
  deprecated: 'default',
};

/** 状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  active: '启用',
  inactive: '禁用',
  draft: '草稿',
  published: '已发布',
  deprecated: '已弃用',
};

/**
 * 从扁平列表构建树形结构
 */
function buildTreeData(objects: IndustrialObject[]): DataNode[] {
  const map = new Map<string, DataNode>();
  const roots: DataNode[] = [];

  for (const obj of objects) {
    map.set(obj.id, {
      key: obj.id,
      title: `${obj.display_name}（${obj.code}）`,
      children: [],
    });
  }

  for (const obj of objects) {
    const node = map.get(obj.id);
    if (!node) continue;
    if (obj.parent_id && map.has(obj.parent_id)) {
      const parent = map.get(obj.parent_id)!;
      (parent.children as DataNode[]).push(node);
    } else {
      roots.push(node);
    }
  }

  return roots;
}

/**
 * 工业对象图谱页面
 *
 * 功能：
 * - Ant Design Table 列表（编码 / 中文名 / 英文名 / 类型 / 状态 / 操作）
 * - 顶部「新建对象」按钮 + 类型筛选
 * - Modal + Form 创建弹窗
 * - 子代对象抽屉（Tree 可视化）
 * - 关系管理抽屉（Table）
 */
export function ObjectGraphPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [descendantsDrawerObj, setDescendantsDrawerObj] = useState<IndustrialObject | null>(null);
  const [relationsDrawerObj, setRelationsDrawerObj] = useState<IndustrialObject | null>(null);

  // ---- 数据查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['objects', typeFilter],
    queryFn: () => apiListObjects({ object_type: typeFilter, page_size: 50 }),
  });

  const items: IndustrialObject[] = data?.items ?? [];

  // ---- 子代查询 ----
  const { data: descendantsResp } = useQuery({
    queryKey: ['object-descendants', descendantsDrawerObj?.id],
    queryFn: () => apiGetObjectDescendants(descendantsDrawerObj!.id),
    enabled: !!descendantsDrawerObj,
  });

  /** 从已加载的对象列表中，按 descendant_ids 过滤出完整对象用于构建树 */
  const descendantObjects: IndustrialObject[] = (() => {
    const ids = descendantsResp?.descendant_ids ?? [];
    if (ids.length === 0) return [];
    const idSet = new Set(ids);
    return items.filter((obj) => idSet.has(obj.id));
  })();

  // ---- 关系查询 ----
  const { data: relations } = useQuery({
    queryKey: ['object-relations', relationsDrawerObj?.id],
    queryFn: () => apiListObjectRelations(relationsDrawerObj!.id),
    enabled: !!relationsDrawerObj,
  });

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateObject,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['objects'] });
      setModalOpen(false);
      form.resetFields();
      message.success('对象创建成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 事件处理 ----
  const handleCreate = (): void => {
    form.resetFields();
    setModalOpen(true);
  };

  const handleSubmit = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      createMutation.mutate({
        display_name: values.display_name,
        object_type: values.object_type,
        description: values.description,
      });
    } catch {
      // 表单校验失败
    }
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<IndustrialObject> = [
    {
      title: '编码',
      dataIndex: 'code',
      key: 'code',
      width: 160,
    },
    {
      title: '名称',
      dataIndex: 'display_name',
      key: 'display_name',
    },
    {
      title: '类型',
      dataIndex: 'object_type',
      key: 'object_type',
      width: 100,
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
      width: 200,
      render: (_: unknown, record: IndustrialObject) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => setDescendantsDrawerObj(record)}
          >
            子代
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => setRelationsDrawerObj(record)}
          >
            关系
          </Button>
        </Space>
      ),
    },
  ];

  // ---- 关系列定义 ----
  const relationColumns: ColumnsType<ObjectRelation> = [
    {
      title: '目标对象',
      dataIndex: 'target_id',
      key: 'target_id',
    },
    {
      title: '关系类型',
      dataIndex: 'relation_type',
      key: 'relation_type',
      width: 140,
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (active: boolean) => (
        <Tag color={active ? 'green' : 'default'}>
          {active ? '活跃' : '已禁用'}
        </Tag>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          新建对象
        </Button>
        <Select
          placeholder="对象类型"
          allowClear
          style={{ width: 140 }}
          value={typeFilter}
          onChange={(val: string | undefined) => setTypeFilter(val)}
          options={[
            { value: 'lab', label: '实验室' },
            { value: 'production_line', label: '产线' },
            { value: 'equipment_group', label: '设备组' },
            { value: 'instrument', label: '仪器' },
            { value: 'measurement_point', label: '测量点' },
          ]}
        />
      </Space>

      <Table<IndustrialObject>
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={isLoading}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        size="middle"
      />

      {/* 创建对象 Modal */}
      <Modal
        title="新建对象"
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        confirmLoading={createMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="display_name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="如：研发中心" maxLength={200} />
          </Form.Item>
          <Form.Item
            name="object_type"
            label="对象类型"
            rules={[{ required: true, message: '请选择对象类型' }]}
          >
            <Select
              placeholder="选择对象类型"
              options={[
                { value: 'lab', label: '实验室' },
                { value: 'production_line', label: '产线' },
                { value: 'equipment_group', label: '设备组' },
                { value: 'instrument', label: '仪器' },
                { value: 'measurement_point', label: '测量点' },
              ]}
            />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea placeholder="对象描述（可选）" rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 子代对象 Drawer */}
      <Drawer
        title="子代对象"
        open={!!descendantsDrawerObj}
        onClose={() => setDescendantsDrawerObj(null)}
        width={500}
      >
        {descendantsDrawerObj && (
          <>
            <Title level={5}>
              {descendantsDrawerObj.display_name}（{descendantsDrawerObj.code}）的子代
            </Title>
            {descendantObjects.length > 0 && (
              <Tree
                treeData={buildTreeData(descendantObjects)}
                defaultExpandAll
                showLine
              />
            )}
            {descendantsResp && descendantsResp.descendant_ids.length > descendantObjects.length && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">
                  以下 {descendantsResp.descendant_ids.length - descendantObjects.length} 个后代 ID 未在当前列表中加载：
                </Text>
                <div style={{ marginTop: 4 }}>
                  {descendantsResp.descendant_ids
                    .filter((id) => !descendantObjects.some((obj) => obj.id === id))
                    .map((id) => (
                      <Tag key={id} style={{ marginBottom: 4 }}>{id}</Tag>
                    ))}
                </div>
              </div>
            )}
            {(!descendantsResp || descendantsResp.descendant_ids.length === 0) && (
              <Text type="secondary">暂无子代对象</Text>
            )}
          </>
        )}
      </Drawer>

      {/* 关系管理 Drawer */}
      <Drawer
        title="关系管理"
        open={!!relationsDrawerObj}
        onClose={() => setRelationsDrawerObj(null)}
        width={600}
      >
        {relationsDrawerObj && (
          <>
            <Title level={5}>
              {relationsDrawerObj.display_name}（{relationsDrawerObj.code}）的关系
            </Title>
            <Table<ObjectRelation>
              columns={relationColumns}
              dataSource={Array.isArray(relations) ? relations.map((r) => ({ ...r, key: r.id })) : []}
              pagination={false}
              size="small"
            />
            {(!relations || relations.length === 0) && (
              <Text type="secondary">暂无关系</Text>
            )}
          </>
        )}
      </Drawer>
    </div>
  );
}
