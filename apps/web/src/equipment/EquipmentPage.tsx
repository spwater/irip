import { useEffect, useState } from 'react';
import {
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';

const { Text } = Typography;
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateEquipment,
  apiGetEquipment,
  apiDeleteEquipment,
  apiListEquipment,
  apiUpdateEquipment,
  apiUpdateEquipmentStatus,
  type EquipmentListItem,
} from '@/api/equipment-flows';
import { apiGetDepartmentNameMap, apiListDepartments } from '@/api/departments';
import { ExperimentalObjectPage } from '@/objects/ExperimentalObjectPage';
import { extractApiError } from '@/api/types';
import { DataTableShell } from '@/components/ui';

/**
 * 设备仪器管理页面
 *
 * 功能：
 * - Ant Design Table 列表（编码 / 名称 / 所属机构 / 状态 / 物理量数 / 排序 / 操作）
 * - 顶部"新建仪器或方法"按钮 + 状态筛选 Select + 机构筛选 Select
 * - Modal + Form 创建/编辑弹窗（code 编辑时 disabled）
 * - 物理量管理 Drawer（多选已发布物理量，全量替换）
 * - Popconfirm 启用/禁用确认
 */
export function EquipmentPage({
  presetDeptId,
  onPresetDeptIdConsumed,
}: {
  presetDeptId?: string;
  onPresetDeptIdConsumed?: () => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<EquipmentListItem | null>(null);
  // 对象抽屉：就地新建实验对象，不跳转页面
  const [objDrawerOpen, setObjDrawerOpen] = useState(false);
  const [objDrawerEquipId, setObjDrawerEquipId] = useState<string | undefined>(undefined);
  const [form] = Form.useForm();

  // 当 presetDeptId 变化时，自动打开新建弹窗并预填
  useEffect(() => {
    if (presetDeptId) {
      setEditingItem(null);
      form.resetFields();
      form.setFieldsValue({ department_id: presetDeptId, visible_departments: [] });
      setModalOpen(true);
      onPresetDeptIdConsumed?.();
    }
  }, [presetDeptId]);

  // ---- 数据查询：设备列表 ----
  const { data, isLoading } = useQuery({
    queryKey: ['equipment', statusFilter, deptFilter],
    queryFn: () =>
      apiListEquipment({ status: statusFilter, department_id: deptFilter, limit: 100 }),
  });

  const items: EquipmentListItem[] = data?.items ?? [];

  // ---- 数据查询：部门列表（用于筛选 + 表单下拉） ----
  const { data: deptData } = useQuery({
    queryKey: ['departments', undefined],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });

  const deptOptions = (deptData?.items ?? []).map((d) => ({
    value: d.id,
    label: d.display_name,
  }));

  // ---- 数据查询：全部门名称映射（不受部门隔离限制，用于可见单位名称展示）----
  const { data: deptNameMapData } = useQuery({
    queryKey: ['department-name-map'],
    queryFn: apiGetDepartmentNameMap,
  });

  // 部门 ID → 名称映射（完整，不受隔离限制），用于列表中展示可见单位名称
  const deptMap = new Map(
    (deptNameMapData ?? []).map((d) => [d.id, d.display_name] as const),
  );

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateEquipment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['equipment'] });
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      void queryClient.invalidateQueries({ queryKey: ['equipment-for-object-link'] });
      void queryClient.invalidateQueries({ queryKey: ['equipment-for-object-link'] });
      setModalOpen(false);
      form.resetFields();
      message.success('设备创建成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 编辑 Mutation ----
  const updateMutation = useMutation({
    mutationFn: (params: {
      id: string;
      body: {
        display_name: string;
        description?: string;
        department_id?: string;
        visible_departments?: string[];
        sort_order?: number;
        lock_version: number;
      };
    }) => apiUpdateEquipment(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['equipment'] });
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      void queryClient.invalidateQueries({ queryKey: ['equipment-for-object-link'] });
      setModalOpen(false);
      setEditingItem(null);
      form.resetFields();
      message.success('设备更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 状态切换 Mutation ----
  const statusMutation = useMutation({
    mutationFn: (params: {
      id: string;
      body: { status: string; lock_version: number };
    }) => apiUpdateEquipmentStatus(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['equipment'] });
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      void queryClient.invalidateQueries({ queryKey: ['equipment-for-object-link'] });
      message.success('状态更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 删除 Mutation ----
  const deleteMutation = useMutation({
    mutationFn: apiDeleteEquipment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['equipment'] });
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      void queryClient.invalidateQueries({ queryKey: ['equipment-for-object-link'] });
      message.success('设备已删除');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 事件处理 ----

  const handleCreate = (): void => {
    setEditingItem(null);
    form.resetFields();
    form.setFieldsValue({ sort_order: 0, visible_departments: [] });
    setModalOpen(true);
  };

  const handleEdit = async (record: EquipmentListItem): Promise<void> => {
    const detail = await apiGetEquipment(record.id);
    setEditingItem(record);
    form.setFieldsValue({
      code: record.code,
      display_name: record.display_name,
      description: detail.description ?? '',
      department_id: record.department_id,
      visible_departments: detail.visible_departments ?? [],
      sort_order: record.sort_order,
    });
    setModalOpen(true);
  };

  const handleSubmit = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      if (editingItem) {
        const detail = await apiGetEquipment(editingItem.id);
        updateMutation.mutate({
          id: editingItem.id,
          body: {
            display_name: values.display_name,
            description: values.description ?? detail.description ?? null,
            department_id: values.department_id,
            visible_departments: values.visible_departments ?? [],
            sort_order: values.sort_order ?? 0,
            lock_version: detail.lock_version,
          },
        });
      } else {
        createMutation.mutate({
          display_name: values.display_name,
          description: values.description ?? null,
          department_id: values.department_id,
          visible_departments: values.visible_departments ?? [],
          sort_order: values.sort_order ?? 0,
        });
      }
    } catch {
      // 表单校验失败，不提交
    }
  };

  const handleToggleStatus = (record: EquipmentListItem): void => {
    void (async () => {
      const detail = await apiGetEquipment(record.id);
      statusMutation.mutate({
        id: record.id,
        body: {
          status: record.status === 'active' ? 'disabled' : 'active',
          lock_version: detail.lock_version,
        },
      });
    })();
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<EquipmentListItem> = [
    {
      title: '名称',
      key: 'name',
      width: 180,
      render: (_: unknown, record: EquipmentListItem) => (
        <Tooltip title={record.description || undefined} placement="topLeft">
          <Space size={6}>
            <Text strong>{record.display_name}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {record.code}
            </Text>
          </Space>
        </Tooltip>
      ),
    },
    {
      title: '所属单位',
      dataIndex: 'department_name',
      key: 'department_name',
      width: 107,
      render: (name: string) =>
        name ? (
          <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
            {name}
          </Tag>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '可见单位',
      key: 'visible_departments',
      width: 293,
      render: (_: unknown, record: EquipmentListItem) => {
        const ids = record.visible_departments ?? [];
        if (!ids.length) {
          return <Text type="secondary">-</Text>;
        }
        return (
          <Space size={4} wrap>
            {ids.map((id) => (
              <Tag key={id} color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                {deptMap.get(id) ?? id.slice(0, 8)}
              </Tag>
            ))}
          </Space>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) =>
        status === 'active' ? (
          <Tag color="green">启用</Tag>
        ) : (
          <Tag color="default" style={{ opacity: 0.5 }}>
            禁用
          </Tag>
        ),
    },
    {
      title: '排序',
      dataIndex: 'sort_order',
      key: 'sort_order',
      width: 80,
      align: 'center',
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_: unknown, record: EquipmentListItem) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => handleEdit(record)}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => {
              setObjDrawerEquipId(record.id);
              setObjDrawerOpen(true);
            }}
          >
            +对象
          </Button>
          <Popconfirm
            title={
              record.status === 'active'
                ? '确定禁用该设备？'
                : '确定启用该设备？'
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
      <Space style={{ marginBottom: 16 }} wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            新建仪器或方法
          </Button>
          <Select
            placeholder="状态筛选"
            style={{ width: 140 }}
            value={statusFilter ?? '__all__'}
            onChange={(val: string) => setStatusFilter(val === '__all__' ? undefined : val)}
            options={[
              { value: '__all__', label: '全部' },
              { value: 'active', label: '启用' },
              { value: 'disabled', label: '禁用' },
            ]}
          />
          <Select
            placeholder="机构筛选"
            style={{ width: 200 }}
            value={deptFilter ?? '__all__'}
            onChange={(val: string) => setDeptFilter(val === '__all__' ? undefined : val)}
            options={[{ value: '__all__', label: '全部' }, ...deptOptions]}
          />
      </Space>

      <DataTableShell bodyPadding={0}>
        <Table<EquipmentListItem>
          columns={columns}
          dataSource={items}
          rowKey="id"
          loading={isLoading}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="middle"
        />
      </DataTableShell>

      {/* 创建/编辑 Modal */}
      <Modal
        title={editingItem ? '编辑仪器或方法' : '新建仪器或方法'}
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
                title="确定删除该仪器？"
                description="将同时删除仪器及其关联，此操作不可撤销。"
                onConfirm={() => {
                  deleteMutation.mutate(editingItem.id);
                  setModalOpen(false);
                  setEditingItem(null);
                  form.resetFields();
                }}
                okText="确定删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button
                  danger
                  type="primary"
                  loading={deleteMutation.isPending}
                >
                  删除仪器
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
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="display_name"
            label="设备名称"
            rules={[
              { required: true, message: '请输入设备名称' },
              { max: 200, message: '名称不超过 200 字符' },
            ]}
          >
            <Input placeholder="如：光谱仪" maxLength={200} />
          </Form.Item>
          <Form.Item
            name="description"
            label="描述"
            rules={[{ max: 2000, message: '描述不超过 2000 字符' }]}
          >
            <Input.TextArea
              placeholder="设备描述（可选）"
              maxLength={2000}
              rows={3}
            />
          </Form.Item>
          <Form.Item
            name="department_id"
            label="所属机构"
            rules={[{ required: true, message: '请选择所属机构' }]}
          >
            <Select
              placeholder="选择所属机构"
              options={deptOptions}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item
            name="visible_departments"
            label="可见单位"
            tooltip="选择除所属机构外，哪些实验室也可以看到该设备。所属机构默认可见，无需重复选择。"
          >
            <Select
              mode="multiple"
              placeholder="选择可见单位（可多选）"
              options={deptOptions}
              showSearch
              optionFilterProp="label"
              allowClear
            />
          </Form.Item>
          <Form.Item
            name="sort_order"
            label="排序权重"
          >
            <InputNumber min={0} style={{ width: '100%' }} placeholder="默认 0" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 物理量管理 Drawer */}

      {/* 新建实验对象抽屉：就地操作，不跳转页面 */}
      <Drawer
        title="新建实验对象"
        open={objDrawerOpen}
        onClose={() => {
          setObjDrawerOpen(false);
          setObjDrawerEquipId(undefined);
          void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
        }}
        width={960}
        destroyOnClose
      >
        <ExperimentalObjectPage
          presetEquipmentId={objDrawerEquipId}
          onPresetConsumed={() => setObjDrawerEquipId(undefined)}
        />
      </Drawer>
    </div>
  );
}
