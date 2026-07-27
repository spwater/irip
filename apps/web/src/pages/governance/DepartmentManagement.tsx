import { useState } from 'react';
import {
  Button,
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

const { Text } = Typography;
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateDepartment,
  apiDeleteDepartment,
  apiListDepartments,
  apiUpdateDepartment,
  apiUpdateDepartmentStatus,
  type DepartmentListItem,
} from '@/api/client';
import { useAuthStore } from '@/auth/AuthProvider';
import { MemberDrawer } from '@/pages/governance/MemberDrawer';

/**
 * 树形节点类型：DepartmentListItem + children 数组。
 */
type DepartmentTreeNode = DepartmentListItem & { children?: DepartmentTreeNode[]; level?: number };

/**
 * 实验室管理组件（P0）
 *
 * 功能：
 * - Ant Design Table 树形列表（编码 / 名称 / 状态 / 成员数 / 子部门数 / 仪器数 / 排序 / 操作）
 * - 按 sort_order + created_at 排序
 * - 顶部"新建组织机构"按钮 + 状态筛选 Select
 * - Modal + Form 创建/编辑弹窗（code 编辑时 disabled）
 * - 新建/编辑可选上级部门（Select 树形选项）
 * - 编辑时排除自己及子孙防循环引用
 * - Popconfirm 启用/禁用确认
 * - 禁用行灰色标签
 * - 成员管理抽屉（P1，MemberDrawer）
 */

/**
 * 将扁平列表构建为树形结构。
 *
 * @param items 扁平的部门列表项。
 * @returns 根节点列表（每个节点附带 children 数组）。
 */
function buildTree(items: DepartmentListItem[]): DepartmentTreeNode[] {
  const map = new Map<string, DepartmentTreeNode>();
  items.forEach((item) => map.set(item.id, { ...item, children: [], level: 0 }));
  const roots: DepartmentTreeNode[] = [];
  map.forEach((item) => {
    if (item.parent_id && map.has(item.parent_id)) {
      const parent = map.get(item.parent_id)!;
      item.level = (parent.level ?? 0) + 1;
      parent.children!.push(item);
    } else {
      roots.push(item);
    }
  });
  map.forEach((item) => {
    if (item.children && item.children.length === 0) {
      delete item.children;
    }
  });
  return roots;
}

/**
 * 获取指定部门的所有后代 ID（含自身）。
 *
 * 用于编辑时排除自己及子孙作为上级选项，防止循环引用。
 *
 * @param items 扁平的部门列表项。
 * @param id 要查找后代的部门 ID。
 * @returns 包含该部门及其所有后代 ID 的 Set。
 */
function getDescendantIds(
  items: DepartmentListItem[],
  id: string,
): Set<string> {
  const result = new Set<string>([id]);
  const queue = [id];
  while (queue.length > 0) {
    const current = queue.shift()!;
    items.forEach((item) => {
      if (item.parent_id === current && !result.has(item.id)) {
        result.add(item.id);
        queue.push(item.id);
      }
    });
  }
  return result;
}

export function DepartmentManagement({
  onAddEquipment,
}: {
  onAddEquipment?: (deptId: string) => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.roles?.includes('platform_administrator') ?? false;
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingDept, setEditingDept] = useState<DepartmentListItem | null>(null);
  const [memberDrawerDept, setMemberDrawerDept] = useState<DepartmentListItem | null>(null);
  const [form] = Form.useForm();

  // ---- 数据查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['departments', statusFilter],
    queryFn: () => apiListDepartments({ status: statusFilter, limit: 100 }),
  });

  const items: DepartmentListItem[] = data?.items ?? [];

  // 树形数据
  const treeData = buildTree(items);

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateDepartment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      void queryClient.refetchQueries({ queryKey: ['departments'] });
      setModalOpen(false);
      form.resetFields();
      message.success('实验室创建成功');
    },
    onError: (err: unknown) => {
      const msg = _extractErrorMessage(err);
      message.error(msg);
    },
  });

  // ---- 编辑 Mutation ----
  const updateMutation = useMutation({
    mutationFn: (params: {
      id: string;
      body: {
        display_name: string;
        description?: string;
        sort_order: number;
        lock_version: number;
        parent_id?: string | null;
      };
    }) => apiUpdateDepartment(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      void queryClient.refetchQueries({ queryKey: ['departments'] });
      setModalOpen(false);
      setEditingDept(null);
      form.resetFields();
      message.success('实验室更新成功');
    },
    onError: (err: unknown) => {
      const msg = _extractErrorMessage(err);
      message.error(msg);
    },
  });

  // ---- 状态切换 Mutation ----
  const statusMutation = useMutation({
    mutationFn: (params: {
      id: string;
      body: { status: 'active' | 'disabled'; lock_version: number };
    }) => apiUpdateDepartmentStatus(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      void queryClient.refetchQueries({ queryKey: ['departments'] });
      message.success('状态更新成功');
    },
    onError: (err: unknown) => {
      const msg = _extractErrorMessage(err);
      message.error(msg);
    },
  });

  // ---- 删除 Mutation ----
  const deleteMutation = useMutation({
    mutationFn: apiDeleteDepartment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      void queryClient.refetchQueries({ queryKey: ['departments'] });
      setModalOpen(false);
      setEditingDept(null);
      form.resetFields();
      message.success('部门已删除');
    },
    onError: (err: unknown) => {
      const msg = _extractErrorMessage(err);
      message.error(msg);
    },
  });

  // ---- 上级部门选项（编辑时排除自己及子孙防循环）----
  const excludeIds = editingDept
    ? getDescendantIds(items, editingDept.id)
    : new Set<string>();
  const parentOptions = items
    .filter((item) => !excludeIds.has(item.id))
    .map((item) => ({
      value: item.id,
      label: item.display_name,
    }));

  // ---- 事件处理 ----

  const handleCreate = (): void => {
    setEditingDept(null);
    form.resetFields();
    form.setFieldsValue({ sort_order: 0, parent_id: undefined });
    setModalOpen(true);
  };

  const handleEdit = async (record: DepartmentListItem): Promise<void> => {
    // 获取详情以拿到 lock_version + description + parent_id（列表项不含这些字段）
    const { apiGetDepartment } = await import('@/api/client');
    const detail = await apiGetDepartment(record.id);
    setEditingDept(record);
    form.setFieldsValue({
      code: record.code,
      display_name: record.display_name,
      description: detail.description ?? '',
      sort_order: record.sort_order,
      parent_id: detail.parent_id,
    });
    setModalOpen(true);
  };

  const handleSubmit = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      if (editingDept) {
        // 编辑：需要 lock_version，从详情接口获取
        const { apiGetDepartment } = await import('@/api/client');
        const detail = await apiGetDepartment(editingDept.id);
        updateMutation.mutate({
          id: editingDept.id,
          body: {
            display_name: values.display_name,
            description: values.description ?? detail.description ?? null,
            sort_order: values.sort_order ?? 0,
            lock_version: detail.lock_version,
            parent_id: values.parent_id ?? null,
          },
        });
      } else {
        // 创建
        createMutation.mutate({
          display_name: values.display_name,
          description: values.description ?? null,
          sort_order: values.sort_order ?? 0,
          parent_id: values.parent_id ?? null,
        });
      }
    } catch {
      // 表单校验失败，不提交
    }
  };

  const handleToggleStatus = (record: DepartmentListItem): void => {
    // 需要获取 lock_version
    void (async () => {
      const { apiGetDepartment } = await import('@/api/client');
      const detail = await apiGetDepartment(record.id);
      statusMutation.mutate({
        id: record.id,
        body: {
          status: record.status === 'active' ? 'disabled' : 'active',
          lock_version: detail.lock_version,
        },
      });
    })();
  };

  const handleDelete = (): void => {
    if (!editingDept) return;
    deleteMutation.mutate(editingDept.id);
  };

  // ---- 表格列定义 ----
  const columns: ColumnsType<DepartmentTreeNode> = [
    {
      title: '名称',
      dataIndex: 'display_name',
      key: 'display_name',
      width: 180,
      render: (name: string, record: DepartmentTreeNode) => (
        <Tooltip title={record.description || undefined} placement="topLeft">
          <Space>
            <Text strong>{name}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>{record.code}</Text>
          </Space>
        </Tooltip>
      ),
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
          <Tag color="default" style={{ opacity: 0.5 }}>禁用</Tag>
        ),
    },
    {
      title: '成员数',
      dataIndex: 'member_count',
      key: 'member_count',
      width: 80,
      align: 'center',
    },
    {
      title: '子部门数',
      dataIndex: 'children_count',
      key: 'children_count',
      width: 90,
      align: 'center',
    },
    {
      title: '仪器数',
      dataIndex: 'equipment_count',
      key: 'equipment_count',
      width: 80,
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
      width: 300,
      render: (_: unknown, record: DepartmentTreeNode) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => handleEdit(record)}
            disabled={!isAdmin}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => onAddEquipment?.(record.id)}
            disabled={!isAdmin}
          >
            +仪器
          </Button>
          <Popconfirm
            title={record.status === 'active' ? '确定禁用该实验室？' : '确定启用该实验室？'}
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
            onClick={() => setMemberDrawerDept(record)}
          >
            成员
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={handleCreate} disabled={!isAdmin}>
          新建组织机构
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
      </Space>

      <Table<DepartmentTreeNode>
        columns={columns}
        dataSource={treeData}
        rowKey="id"
        loading={isLoading}
        pagination={false}
        size="middle"
        scroll={{ y: 600 }}
        expandable={{
          childrenColumnName: 'children',
          defaultExpandAllRows: true,
        }}
      />

      <Modal
        title={editingDept ? '编辑组织机构' : '新建组织机构'}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          setEditingDept(null);
          form.resetFields();
        }}
        footer={
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            {editingDept ? (
              <Tooltip
                title={
                  editingDept.children_count > 0
                    ? '存在子部门，请先删除子部门后才能删除该实验室'
                    : editingDept.equipment_count > 0
                      ? '存在仪器，请先迁移或删除仪器后才能删除该实验室'
                      : '删除后不可恢复'
                }
              >
                <Popconfirm
                  title="确定删除该实验室？"
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
                    disabled={
                      editingDept.children_count > 0 ||
                      editingDept.equipment_count > 0
                    }
                  >
                    删除实验室
                  </Button>
                </Popconfirm>
              </Tooltip>
            ) : (
              <span />
            )}
            <Space>
              <Button
                onClick={() => {
                  setModalOpen(false);
                  setEditingDept(null);
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
            label="实验室名称"
            rules={[
              { required: true, message: '请输入实验室名称' },
              { max: 200, message: '名称不超过 200 字符' },
            ]}
          >
            <Input placeholder="如：材料实验室" maxLength={200} />
          </Form.Item>
          <Form.Item
            name="description"
            label="描述"
            rules={[{ max: 2000, message: '描述不超过 2000 字符' }]}
          >
            <Input.TextArea
              placeholder="实验室描述（可选）"
              maxLength={2000}
              rows={3}
            />
          </Form.Item>
          <Form.Item
            name="parent_id"
            label="上级部门"
            tooltip="选择上级部门构建树形结构，留空表示顶级部门"
          >
            <Select
              placeholder="留空为顶级部门"
              allowClear
              options={parentOptions}
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

      {memberDrawerDept && (
        <MemberDrawer
          department={memberDrawerDept}
          open={!!memberDrawerDept}
          onClose={() => setMemberDrawerDept(null)}
        />
      )}
    </div>
  );
}

/**
 * 从 Axios 错误中提取后端错误消息。
 */
function _extractErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const response = (err as { response?: { data?: { error?: { message?: string } } } }).response;
    if (response?.data?.error?.message) {
      return response.data.error.message;
    }
  }
  if (err instanceof Error) {
    return err.message;
  }
  return '操作失败';
}
