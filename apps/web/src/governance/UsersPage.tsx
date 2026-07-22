import { useState } from 'react';
import {
  Button,
  Form,
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
  apiAssignRoles,
  apiListUsers,
  apiRemoveRole,
  apiUpdateUserStatus,
  extractApiError,
  type UserListItem,
} from '@/api/client';
import { useAuthStore } from '@/auth/AuthProvider';

const { Title, Text } = Typography;

/** 内置角色选项 */
const ROLE_OPTIONS = [
  { value: 'platform_administrator', label: '平台管理员' },
  { value: 'standard_owner', label: '标准负责人' },
  { value: 'data_steward', label: '数据管家' },
  { value: 'researcher', label: '研究员' },
  { value: 'model_engineer', label: '模型工程师' },
  { value: 'reviewer', label: '审核员' },
  { value: 'read_only_user', label: '只读用户' },
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
  const [form] = Form.useForm();

  // 仅 platform_administrator 可访问
  const isAdmin: boolean = user?.roles?.includes('platform_administrator') ?? false;

  // ---- 数据查询：用户列表 ----
  const { data, isLoading } = useQuery({
    queryKey: ['governance', 'users', statusFilter],
    queryFn: () => apiListUsers({ status: statusFilter, limit: 100 }),
    enabled: isAdmin,
  });

  const items: UserListItem[] = data?.items ?? [];

  // ---- 角色分配 Mutation ----
  const assignMutation = useMutation({
    mutationFn: (params: { userId: string; roles: string[] }) =>
      apiAssignRoles(params.userId, params.roles),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['governance', 'users'] });
      setAssignTarget(null);
      form.resetFields();
      message.success('角色分配成功');
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

  // ---- 事件处理 ----

  const handleAssignOpen = (record: UserListItem): void => {
    setAssignTarget(record);
    form.setFieldsValue({
      roles: record.roles ?? [],
    });
  };

  const handleAssignSubmit = async (): Promise<void> => {
    if (!assignTarget) return;
    try {
      const values = await form.validateFields();
      assignMutation.mutate({
        userId: assignTarget.id,
        roles: values.roles as string[],
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
        <Title level={3}>用户管理</Title>
        <Text type="danger">仅平台管理员可访问此页面。</Text>
      </div>
    );
  }

  // ---- 表格列定义 ----
  const columns: ColumnsType<UserListItem> = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 280,
      ellipsis: true,
    },
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
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: unknown, record: UserListItem) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => handleAssignOpen(record)}
          >
            分配角色
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
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={3}>用户管理</Title>
      <Space style={{ marginBottom: 16 }} wrap>
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
      </Space>

      <Table<UserListItem>
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={isLoading}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        size="middle"
      />

      {/* 角色分配 Modal */}
      <Modal
        title={assignTarget ? `分配角色 — ${assignTarget.display_name}` : '分配角色'}
        open={!!assignTarget}
        onOk={handleAssignSubmit}
        onCancel={() => {
          setAssignTarget(null);
          form.resetFields();
        }}
        confirmLoading={assignMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="roles"
            label="角色"
            rules={[{ required: true, message: '请至少选择一个角色' }]}
          >
            <Select
              mode="multiple"
              placeholder="选择要分配的角色"
              style={{ width: '100%' }}
              options={ROLE_OPTIONS}
              optionFilterProp="label"
              showSearch
            />
          </Form.Item>
          <Text type="secondary" style={{ fontSize: 12 }}>
            新选中的角色将合并到用户已有角色列表中，不会移除已有角色。
          </Text>
        </Form>
      </Modal>
    </div>
  );
}
