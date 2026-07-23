import { useState } from 'react';
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateObject,
  apiDeleteObject,
  apiGetObject,
  apiListObjects,
  apiUpdateObject,
  apiUpdateObjectStatus,
  extractApiError,
  type IndustrialObject,
} from '@/api/client';

/**
 * 实验对象管理页面（要素管理第 3 个 Tab）
 *
 * 实验对象 = industrial_object 表中 object_type 为 material / sample / product 的记录。
 * 与设备仪器、物理量平级管理，不走审批流，状态为 active / inactive。
 *
 * 功能：
 * - Ant Design Table 列表（编码 / 名称 / 类型 / 状态 / 描述 / 操作）
 * - 顶部"新建对象"按钮 + 类型筛选
 * - Modal + Form 创建/编辑弹窗（类型创建后锁定）
 * - Popconfirm 启用/禁用确认
 */

/** 实验对象类型选项 */
const EXP_OBJECT_TYPES = [
  { value: 'material', label: '物料' },
  { value: 'sample', label: '样品' },
  { value: 'product', label: '产品' },
];

/** 类型 → 中文标签 */
const TYPE_LABEL: Record<string, string> = {
  material: '物料',
  sample: '样品',
  product: '产品',
};

/** 状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  active: 'green',
  inactive: 'default',
};

/** 状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  active: '启用',
  inactive: '禁用',
};

export function ExperimentalObjectPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<IndustrialObject | null>(null);
  const [form] = Form.useForm();

  // ---- 数据查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['exp-objects', typeFilter],
    queryFn: () =>
      apiListObjects({
        object_type: typeFilter
          ? typeFilter
          : 'material,sample,product',
        page_size: 100,
      }),
  });

  const items: IndustrialObject[] = data?.items ?? [];

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateObject,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      setModalOpen(false);
      form.resetFields();
      message.success('实验对象创建成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 编辑 Mutation ----
  const updateMutation = useMutation({
    mutationFn: (params: {
      id: string;
      body: { display_name: string; description?: string | null };
    }) => apiUpdateObject(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      setModalOpen(false);
      setEditingItem(null);
      form.resetFields();
      message.success('实验对象更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 状态切换 Mutation ----
  const statusMutation = useMutation({
    mutationFn: (params: {
      id: string;
      body: { status: 'active' | 'inactive' };
    }) => apiUpdateObjectStatus(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      message.success('状态更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 删除 Mutation ----
  const deleteMutation = useMutation({
    mutationFn: apiDeleteObject,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      setModalOpen(false);
      setEditingItem(null);
      form.resetFields();
      message.success('实验对象已删除');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 事件处理 ----
  const handleCreate = (): void => {
    setEditingItem(null);
    form.resetFields();
    setModalOpen(true);
  };

  const handleEdit = async (record: IndustrialObject): Promise<void> => {
    const detail = await apiGetObject(record.id);
    setEditingItem(record);
    form.setFieldsValue({
      code: record.code,
      display_name: detail.display_name,
      object_type: record.object_type,
      description: detail.description ?? '',
    });
    setModalOpen(true);
  };

  const handleSubmit = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      if (editingItem) {
        updateMutation.mutate({
          id: editingItem.id,
          body: {
            display_name: values.display_name,
            description: values.description ?? null,
          },
        });
      } else {
        createMutation.mutate({
          code: values.code,
          display_name: values.display_name,
          object_type: values.object_type,
          description: values.description,
        });
      }
    } catch {
      // 表单校验失败
    }
  };

  const handleToggleStatus = (record: IndustrialObject): void => {
    statusMutation.mutate({
      id: record.id,
      body: {
        status: record.status === 'active' ? 'inactive' : 'active',
      },
    });
  };

  const handleDelete = (): void => {
    if (!editingItem) return;
    deleteMutation.mutate(editingItem.id);
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
      render: (t: string) => TYPE_LABEL[t] ?? t,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] ?? 'default'}>
          {STATUS_LABEL[s] ?? s}
        </Tag>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (d: string | null) => d ?? '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: unknown, record: IndustrialObject) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => handleEdit(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title={
              record.status === 'active'
                ? '确定禁用该对象？'
                : '确定启用该对象？'
            }
            onConfirm={() => handleToggleStatus(record)}
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
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={handleCreate}>
          新建实验对象
        </Button>
        <Select
          placeholder="对象类型"
          allowClear
          style={{ width: 140 }}
          value={typeFilter}
          onChange={(val: string | undefined) => setTypeFilter(val)}
          options={EXP_OBJECT_TYPES}
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

      {/* 创建/编辑 Modal */}
      <Modal
        title={editingItem ? '编辑实验对象' : '新建实验对象'}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          setEditingItem(null);
          form.resetFields();
        }}
        footer={
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            {editingItem ? (
              <Popconfirm
                title="确定删除该实验对象？"
                description="此操作不可恢复"
                onConfirm={handleDelete}
                okText="确定删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button
                  danger
                  type="primary"
                  loading={deleteMutation.isPending}
                >
                  删除对象
                </Button>
              </Popconfirm>
            ) : (
              <span />
            )}
            <Space>
              <Button
                onClick={() => {
                  setModalOpen(false);
                  setEditingItem(null);
                  form.resetFields();
                }}
              >
                取消
              </Button>
              <Button
                type="primary"
                onClick={handleSubmit}
                loading={createMutation.isPending || updateMutation.isPending}
              >
                保存
              </Button>
            </Space>
          </div>
        }
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="code"
            label="对象编码"
            rules={[
              { required: true, message: '请输入对象编码' },
              {
                pattern: /^[a-zA-Z][a-zA-Z0-9_-]*$/,
                message: '字母/数字/下划线/连字符，首字符必须为字母',
              },
            ]}
            extra={editingItem ? '编码创建后锁定，不可修改' : undefined}
          >
            <Input
              placeholder="如：aluminum_alloy"
              disabled={!!editingItem}
              maxLength={64}
            />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="如：铝合金" maxLength={200} />
          </Form.Item>
          <Form.Item
            name="object_type"
            label="类型"
            rules={[{ required: true, message: '请选择类型' }]}
            extra={editingItem ? '类型创建后锁定，不可修改' : undefined}
          >
            <Select
              placeholder="选择实验对象类型"
              options={EXP_OBJECT_TYPES}
              disabled={!!editingItem}
            />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea
              placeholder="对象描述（可选）"
              rows={3}
              maxLength={2000}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
