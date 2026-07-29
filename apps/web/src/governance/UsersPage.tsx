import { useState } from 'react';
import {
  Button,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateUser,
  apiDeleteUser,
  apiListUsers,
  apiRemoveRole,
  apiUpdateUser,
  apiUpdateUserStatus,
  type UserListItem,
} from '@/api/governance';
import { apiListDepartments, type DepartmentListItem } from '@/api/departments';
import { extractApiError } from '@/api/types';
import { useAuthStore } from '@/auth/AuthProvider';
import { ActionBar, DataTableShell } from '@/components/ui';

const { Text } = Typography;

/** 内置角色选项 */
const ROLE_OPTIONS = [
  { value: 'platform_administrator', label: '平台管理员' },
  { value: 'platform_auditor', label: '平台监督员（只读）' },
  { value: 'lab_director', label: '实验室负责人' },
  { value: 'lab_member', label: '实验室成员' },
  { value: 'lab_viewer', label: '实验室成员（只读）' },
];

/**
 * 用户管理页面 — 仅管理员可见
 *
 * 功能：
 * - Table: 用户列表（ID / 邮箱 / 显示名 / 角色 / 状态）
 * - 角色分配操作（Modal + Select 多选）
 * - 启用/禁用操作（Popconfirm）
 */
export function UsersPage(): JSX.Element {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [assignTarget, setAssignTarget] = useState<UserListItem | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [createForm] = Form.useForm();

  // 仅 platform_administrator 可访问
  const isAdmin: boolean = user?.roles?.includes('platform_administrator') ?? false;

  // ---- 数据查询：用户列表 ----
  const { data, isLoading } = useQuery({
    queryKey: ['governance', 'users', statusFilter],
    queryFn: () => apiListUsers({ status: statusFilter, limit: 100 }),
    enabled: isAdmin,
  });

  // ---- 数据查询：实验室列表（用于新建账号时选择 + 表格列展示名称）----
  const { data: deptData } = useQuery({
    queryKey: ['departments', 'all'],
    queryFn: () => apiListDepartments({ limit: 100 }),
    enabled: isAdmin,
  });

  const departments: DepartmentListItem[] = deptData?.items ?? [];

  /** 根据 department_id 查找实验室显示名 */
  const getDeptName = (deptId: string | null): string | null => {
    if (!deptId) return null;
    const dept = departments.find((d) => d.id === deptId);
    return dept?.display_name ?? null;
  };

  const items: UserListItem[] = data?.items ?? [];

  // ---- 编辑用户 Mutation ----
  // ---- 新建用户 Mutation ----
  const createMutation = useMutation({
    mutationFn: (params: { email: string; display_name: string; password: string; roles: string[]; department_id?: string }) =>
      apiCreateUser(params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['governance', 'users'] });
      setCreateModalOpen(false);
      createForm.resetFields();
      message.success('用户创建成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 编辑用户 Mutation ----
  const updateMutation = useMutation({
    mutationFn: (params: { userId: string; display_name?: string; password?: string; roles?: string[]; department_id?: string | null }) =>
      apiUpdateUser(params.userId, {
        display_name: params.display_name,
        password: params.password,
        roles: params.roles,
        department_id: params.department_id,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['governance', 'users'] });
      setAssignTarget(null);
      form.resetFields();
      message.success('用户信息更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 移除角色 Mutation ----
  const removeRoleMutation = useMutation({
    mutationFn: (params: { userId: string; role: string }) =>
      apiRemoveRole(params.userId, params.role),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['governance', 'users'] });
      message.success('角色移除成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 状态切换 Mutation ----
  const statusMutation = useMutation({
    mutationFn: (params: { userId: string; status: 'active' | 'disabled' }) =>
      apiUpdateUserStatus(params.userId, params.status),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['governance', 'users'] });
      message.success('状态更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 删除用户 Mutation ----
  const deleteMutation = useMutation({
    mutationFn: (userId: string) => apiDeleteUser(userId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['governance', 'users'] });
      message.success('用户已删除');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 事件处理 ----

  const handleAssignOpen = (record: UserListItem): void => {
    setAssignTarget(record);
    form.setFieldsValue({
      display_name: record.display_name,
      password: undefined,
      roles: record.roles ?? [],
      department_id: record.department_id ?? undefined,
    });
  };

  const handleCreateSubmit = async (): Promise<void> => {
    try {
      const values = await createForm.validateFields();
      createMutation.mutate({
        email: values.email,
        display_name: values.display_name,
        password: values.password,
        roles: values.roles as string[],
        department_id: values.department_id || undefined,
      });
    } catch {
      // 表单校验失败
    }
  };

  const handleAssignSubmit = async (): Promise<void> => {
    if (!assignTarget) return;
    try {
      const values = await form.validateFields();
      updateMutation.mutate({
        userId: assignTarget.id,
        display_name: values.display_name,
        password: values.password || undefined,
        roles: values.roles as string[],
        department_id: values.department_id || null,
      });
    } catch {
      // 表单校验失败
    }
  };

  const handleToggleStatus = (record: UserListItem): void => {
    const newStatus = record.status === 'active' ? 'disabled' : 'active';
    statusMutation.mutate({ userId: record.id, status: newStatus });
  };

  const handleRemoveRole = (record: UserListItem, role: string): void => {
    removeRoleMutation.mutate({ userId: record.id, role });
  };

  // ---- 权限检查 ----
  if (!isAdmin) {
    return (
      <div>
        <Text type="danger">仅平台管理员可访问此页面。</Text>
      </div>
    );
  }

  // ---- 表格列定义 ----
  const columns: ColumnsType<UserListItem> = [
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      width: 200,
    },
    {
      title: '显示名',
      dataIndex: 'display_name',
      key: 'display_name',
    },
    {
      title: '角色',
      dataIndex: 'roles',
      key: 'roles',
      width: 300,
      render: (roles: string[] | undefined, record: UserListItem) => {
        if (!roles || roles.length === 0) {
          return <Text type="secondary">无角色</Text>;
        }
        return (
          <Space size="small" wrap>
            {roles.map((role) => {
              const option = ROLE_OPTIONS.find((r) => r.value === role);
              return (
                <Tag
                  key={role}
                  color="blue"
                  closable
                  onClose={(e) => {
                    e.preventDefault();
                    handleRemoveRole(record, role);
                  }}
                >
                  {option?.label ?? role}
                </Tag>
              );
            })}
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
      title: '实验室',
      dataIndex: 'department_id',
      key: 'department_id',
      width: 160,
      render: (deptId: string | null) => {
        const name = getDeptName(deptId);
        return name ? (
          <Tag color="cyan">{name}</Tag>
        ) : (
          <Text type="secondary">-</Text>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_: unknown, record: UserListItem) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => handleAssignOpen(record)}
          >
            编辑角色
          </Button>
          <Popconfirm
            title={
              record.status === 'active'
                ? '确定禁用该用户？'
                : '确定启用该用户？'
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
          <Popconfirm
            title="确定删除该用户？此操作不可恢复！"
            onConfirm={() => deleteMutation.mutate(record.id)}
            okText="确定"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button
              type="link"
              size="small"
              danger
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <ActionBar style={{ marginBottom: 16 }}>
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
        <Button type="primary" onClick={() => setCreateModalOpen(true)}>
          新建账号
        </Button>
      </ActionBar>

      <DataTableShell bodyPadding={0}>
        <Table<UserListItem>
          columns={columns}
          dataSource={items}
          rowKey="id"
          loading={isLoading}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="middle"
        />
      </DataTableShell>

      {/* 编辑角色 Modal */}
      <Modal
        title={assignTarget ? `编辑角色 — ${assignTarget.display_name}` : '编辑角色'}
        open={!!assignTarget}
        onOk={handleAssignSubmit}
        onCancel={() => {
          setAssignTarget(null);
          form.resetFields();
        }}
        confirmLoading={updateMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item label="邮箱">
            <Input value={assignTarget?.email ?? ''} disabled />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="显示名"
            rules={[{ required: true, message: '请输入显示名' }]}
          >
            <Input placeholder="姓名" />
          </Form.Item>
          <Form.Item
            name="password"
            label="新密码"
            extra="留空则不修改密码"
          >
            <Input.Password placeholder="输入新密码（可选）" />
          </Form.Item>
          <Form.Item
            name="roles"
            label="角色"
            rules={[{ required: true, message: '请至少选择一个角色' }]}
          >
            <Select
              mode="multiple"
              placeholder="选择角色（可多选）"
              style={{ width: '100%' }}
              options={ROLE_OPTIONS}
              optionFilterProp="label"
              showSearch
            />
          </Form.Item>
          <Form.Item
            name="department_id"
            label="所属实验室"
          >
            <Select
              placeholder="选择实验室（可选）"
              style={{ width: '100%' }}
              allowClear
              options={departments.map((d) => ({
                value: d.id,
                label: d.display_name,
              }))}
              optionFilterProp="label"
              showSearch
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 新建账号 Modal */}
      <Modal
        title="新建账号"
        open={createModalOpen}
        onOk={handleCreateSubmit}
        onCancel={() => {
          setCreateModalOpen(false);
          createForm.resetFields();
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="email"
            label="邮箱"
            rules={[
              { required: true, message: '请输入邮箱' },
              { type: 'email', message: '请输入有效邮箱' },
            ]}
          >
            <Input placeholder="user@irip.local" />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="显示名"
            rules={[{ required: true, message: '请输入显示名' }]}
          >
            <Input placeholder="姓名" />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始密码"
            rules={[
              { required: true, message: '请输入初始密码' },
              { min: 6, message: '密码至少 6 位' },
            ]}
          >
            <Input.Password placeholder="至少 6 位" />
          </Form.Item>
          <Form.Item
            name="roles"
            label="角色"
            rules={[{ required: true, message: '请至少选择一个角色' }]}
          >
            <Select
              mode="multiple"
              placeholder="选择角色（可多选）"
              style={{ width: '100%' }}
              options={ROLE_OPTIONS}
              optionFilterProp="label"
              showSearch
            />
          </Form.Item>
          <Form.Item
            name="department_id"
            label="所属实验室"
          >
            <Select
              placeholder="选择实验室（可选）"
              style={{ width: '100%' }}
              allowClear
              options={departments.map((d) => ({
                value: d.id,
                label: d.display_name,
              }))}
              optionFilterProp="label"
              showSearch
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
