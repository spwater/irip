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
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateEquipment,
  apiGetEquipment,
  apiGetEquipmentVariables,
  apiDeleteEquipment,
  apiListDepartments,
  apiListEquipment,
  apiListVariables,
  apiSetEquipmentVariables,
  apiUpdateEquipment,
  apiUpdateEquipmentStatus,
  extractApiError,
  type EquipmentListItem,
  type EquipmentVariable,
} from '@/api/client';

/**
 * 设备仪器管理页面
 *
 * 功能：
 * - Ant Design Table 列表（编码 / 名称 / 所属机构 / 状态 / 物理量数 / 排序 / 操作）
 * - 顶部"新建设备"按钮 + 状态筛选 Select + 机构筛选 Select
 * - Modal + Form 创建/编辑弹窗（code 编辑时 disabled）
 * - 物理量管理 Drawer（多选已发布物理量，全量替换）
 * - Popconfirm 启用/禁用确认
 */
export function EquipmentPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<EquipmentListItem | null>(null);
  const [varDrawerItem, setVarDrawerItem] = useState<EquipmentListItem | null>(null);
  const [form] = Form.useForm();

  // ---- 数据查询：设备列表 ----
  const { data, isLoading } = useQuery({
    queryKey: ['equipment', statusFilter, deptFilter],
    queryFn: () =>
      apiListEquipment({ status: statusFilter, department_id: deptFilter }),
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

  // ---- 数据查询：已发布物理量（用于物理量管理 Drawer） ----
  const { data: publishedVars } = useQuery({
    queryKey: ['variables', 'published', 100],
    queryFn: () => apiListVariables({ status: 'published', limit: 100 }),
    enabled: !!varDrawerItem,
  });

  const publishedVarOptions = (publishedVars?.items ?? []).map((v) => ({
    value: v.id,
    label: `${v.display_name} (${v.code})`,
  }));

  // ---- 数据查询：当前设备已关联的物理量 ----
  const { data: currentVars, isLoading: varsLoading } = useQuery({
    queryKey: ['equipment-variables', varDrawerItem?.id],
    queryFn: () => apiGetEquipmentVariables(varDrawerItem!.id),
    enabled: !!varDrawerItem,
  });

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateEquipment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['equipment'] });
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
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
        sort_order?: number;
        lock_version: number;
      };
    }) => apiUpdateEquipment(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['equipment'] });
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
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
      message.success('设备已删除');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 设置物理量 Mutation ----
  const setVarsMutation = useMutation({
    mutationFn: (params: { id: string; body: { variable_ids: string[] } }) =>
      apiSetEquipmentVariables(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['equipment'] });
      void queryClient.invalidateQueries({
        queryKey: ['equipment-variables'],
      });
      message.success('物理量设置成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 事件处理 ----

  const handleCreate = (): void => {
    setEditingItem(null);
    form.resetFields();
    form.setFieldsValue({ sort_order: 0 });
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
            sort_order: values.sort_order ?? 0,
            lock_version: detail.lock_version,
          },
        });
      } else {
        createMutation.mutate({
          code: values.code,
          display_name: values.display_name,
          description: values.description ?? null,
          department_id: values.department_id,
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

  const handleSaveVariables = (selectedIds: string[]): void => {
    if (!varDrawerItem) return;
    setVarsMutation.mutate({
      id: varDrawerItem.id,
      body: { variable_ids: selectedIds },
    });
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<EquipmentListItem> = [
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
      title: '所属机构',
      dataIndex: 'department_name',
      key: 'department_name',
      width: 160,
      render: (name: string) => name || '-',
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
      title: '物理量数',
      dataIndex: 'variable_count',
      key: 'variable_count',
      width: 90,
      align: 'center',
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
      width: 180,
      render: (_: unknown, record: EquipmentListItem) => (
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
          <Button
            type="link"
            size="small"
            onClick={() => setVarDrawerItem(record)}
          >
            物理量
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" onClick={handleCreate}>
          新建设备
        </Button>
        <Select
          placeholder="状态筛选"
          allowClear
          style={{ width: 140 }}
          value={statusFilter}
          onChange={(val: string | undefined) => setStatusFilter(val)}
          options={[
            { value: 'active', label: '启用' },
            { value: 'disabled', label: '禁用' },
          ]}
        />
        <Select
          placeholder="机构筛选"
          allowClear
          style={{ width: 200 }}
          value={deptFilter}
          onChange={(val: string | undefined) => setDeptFilter(val)}
          options={deptOptions}
        />
      </Space>

      <Table<EquipmentListItem>
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={isLoading}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        size="middle"
      />

      {/* 创建/编辑 Modal */}
      <Modal
        title={editingItem ? '编辑设备' : '新建设备'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => {
          setModalOpen(false);
          setEditingItem(null);
          form.resetFields();
        }}
        confirmLoading={createMutation.isPending || updateMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="code"
            label="设备编码"
            rules={[
              { required: true, message: '请输入设备编码' },
              {
                pattern: /^[a-z][a-z0-9_]*$/,
                message: '仅小写字母/数字/下划线，首字符必须为字母',
              },
            ]}
            extra={editingItem ? '编码创建后锁定，不可修改' : undefined}
          >
            <Input
              placeholder="如：spectrometer_01"
              disabled={!!editingItem}
              maxLength={64}
            />
          </Form.Item>
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
            name="sort_order"
            label="排序权重"
            rules={[{ required: true, message: '请输入排序权重' }]}
          >
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
        {editingItem && (
          <div style={{ marginTop: 16, borderTop: '1px solid #f0f0f0', paddingTop: 12 }}>
            <Popconfirm
              title="确定删除该仪器？"
              description="将同时删除仪器及其物理量关联，此操作不可撤销。"
              onConfirm={() => {
                deleteMutation.mutate(editingItem.id);
                setModalOpen(false);
                setEditingItem(null);
                form.resetFields();
              }}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button
                danger
                loading={deleteMutation.isPending}
              >
                删除仪器
              </Button>
            </Popconfirm>
          </div>
        )}
      </Modal>

      {/* 物理量管理 Drawer */}
      <EquipmentVariableDrawer
        item={varDrawerItem}
        open={!!varDrawerItem}
        onClose={() => setVarDrawerItem(null)}
        currentVars={currentVars ?? []}
        varsLoading={varsLoading}
        varOptions={publishedVarOptions}
        onSave={handleSaveVariables}
        saving={setVarsMutation.isPending}
      />
    </div>
  );
}

/**
 * 设备物理量管理 Drawer 组件。
 */
function EquipmentVariableDrawer(props: {
  item: EquipmentListItem | null;
  open: boolean;
  onClose: () => void;
  currentVars: EquipmentVariable[];
  varsLoading: boolean;
  varOptions: { value: string; label: string }[];
  onSave: (selectedIds: string[]) => void;
  saving: boolean;
}): JSX.Element {
  const { item, open, onClose, currentVars, varsLoading, varOptions, onSave, saving } = props;
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  // 当 currentVars 变化时，同步 selectedIds
  useEffect(() => {
    setSelectedIds(currentVars.map((v) => v.id));
  }, [currentVars]);

  const handleSave = (): void => {
    onSave(selectedIds);
    onClose();
  };

  return (
    <Drawer
      title={item ? `物理量管理 — ${item.display_name}` : '物理量管理'}
      open={open}
      onClose={onClose}
      width={600}
      footer={
        <Space style={{ float: 'right' }}>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" onClick={handleSave} loading={saving}>
            保存
          </Button>
        </Space>
      }
    >
      {varsLoading ? (
        <p style={{ color: '#999' }}>加载中...</p>
      ) : (
        <>
          <p style={{ marginBottom: 16, color: '#666' }}>
            选择该设备能产出的已发布物理量。保存后将全量替换当前关联。
          </p>
          <Select
            mode="multiple"
            placeholder="选择物理量"
            style={{ width: '100%' }}
            value={selectedIds}
            onChange={(vals: string[]) => setSelectedIds(vals)}
            options={varOptions}
            optionFilterProp="label"
            showSearch
          />
          <div style={{ marginTop: 24 }}>
            <p style={{ fontWeight: 600, marginBottom: 8 }}>
              当前已关联（{currentVars.length}）：
            </p>
            {currentVars.length === 0 ? (
              <p style={{ color: '#999' }}>暂无关联物理量</p>
            ) : (
              <Space size="small" wrap>
                {currentVars.map((v) => (
                  <Tag key={v.id} color="blue">
                    {v.name_zh} ({v.code})
                  </Tag>
                ))}
              </Space>
            )}
          </div>
        </>
      )}
    </Drawer>
  );
}
