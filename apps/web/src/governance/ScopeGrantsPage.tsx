import { useState } from 'react';
import {
  Button,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateScopeGrant,
  apiDeleteScopeGrant,
  apiListScopeGrants,
  extractApiError,
  type ScopeGrantListItem,
} from '@/api/client';
import { useAuthStore } from '@/auth/AuthProvider';

const { Title, Text } = Typography;

/**
 * 范围授权页面 — 仅管理员可见
 *
 * 功能：
 * - Table: 授权列表
 * - 创建授权操作（Modal + Form）
 * - 删除授权操作（Popconfirm）
 */
export function ScopeGrantsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();

  // 仅 platform_administrator 可访问
  const isAdmin: boolean = user?.roles?.includes('platform_administrator') ?? false;

  // ---- 数据查询：授权列表 ----
  const { data, isLoading } = useQuery({
    queryKey: ['governance', 'scope-grants'],
    queryFn: () => apiListScopeGrants({ limit: 100 }),
    enabled: isAdmin,
  });

  const items: ScopeGrantListItem[] = data?.items ?? [];

  // ---- 创建授权 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateScopeGrant,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['governance', 'scope-grants'] });
      setModalOpen(false);
      form.resetFields();
      message.success('授权创建成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 删除授权 Mutation ----
  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiDeleteScopeGrant(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['governance', 'scope-grants'] });
      message.success('授权删除成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 事件处理 ----

  const handleCreate = (): void => {
    form.resetFields();
    form.setFieldsValue({
      resource_type: '*',
    });
    setModalOpen(true);
  };

  const handleSubmit = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      createMutation.mutate({
        user_id: values.user_id ?? null,
        role_id: values.role_id ?? null,
        organization_id: values.organization_id,
        object_root_id: values.object_root_id ?? null,
        department_id: values.department_id ?? null,
        resource_type: values.resource_type,
        action: values.action,
        effective_from: values.effective_from ?? null,
        effective_to: values.effective_to ?? null,
      });
    } catch {
      // 表单校验失败
    }
  };

  // ---- 权限检查 ----
  if (!isAdmin) {
    return (
      <div>
        <Title level={3}>范围授权</Title>
        <Text type="danger">仅平台管理员可访问此页面。</Text>
      </div>
    );
  }

  // ---- 表格列定义 ----
  const columns: ColumnsType<ScopeGrantListItem> = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 280,
      ellipsis: true,
    },
    {
      title: '用户 ID',
      dataIndex: 'user_id',
      key: 'user_id',
      width: 280,
      ellipsis: true,
      render: (val: string | null) => val ?? <Text type="secondary">-</Text>,
    },
    {
      title: '角色 ID',
      dataIndex: 'role_id',
      key: 'role_id',
      width: 280,
      ellipsis: true,
      render: (val: string | null) => val ?? <Text type="secondary">-</Text>,
    },
    {
      title: '资源类型',
      dataIndex: 'resource_type',
      key: 'resource_type',
      width: 140,
      render: (val: string) => <Tag color="blue">{val}</Tag>,
    },
    {
      title: '权限操作',
      dataIndex: 'action',
      key: 'action',
      width: 160,
    },
    {
      title: '生效时间',
      key: 'effective',
      width: 320,
      render: (_: unknown, record: ScopeGrantListItem) => {
        const from = record.effective_from
          ? new Date(record.effective_from).toLocaleString()
          : '不限';
        const to = record.effective_to
          ? new Date(record.effective_to).toLocaleString()
          : '不限';
        return <Text style={{ fontSize: 12 }}>{`${from} ~ ${to}`}</Text>;
      },
    },
    {
      title: '操作',
      key: 'action_btn',
      width: 100,
      render: (_: unknown, record: ScopeGrantListItem) => (
        <Popconfirm
          title="确定删除此授权？"
          onConfirm={() => deleteMutation.mutate(record.id)}
          okText="确定"
          cancelText="取消"
        >
          <Button type="link" size="small" danger>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <Title level={3}>范围授权</Title>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={handleCreate}>
          新建授权
        </Button>
      </Space>

      <Table<ScopeGrantListItem>
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={isLoading}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        size="middle"
      />

      {/* 创建授权 Modal */}
      <Modal
        title="新建范围授权"
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        confirmLoading={createMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={560}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="organization_id"
            label="组织 ID"
            rules={[{ required: true, message: '请输入组织 ID' }]}
          >
            <Input placeholder="组织 UUID" />
          </Form.Item>
          <Space style={{ display: 'flex', marginBottom: 0 }} direction="vertical">
            <Form.Item
              name="user_id"
              label="用户 ID（与角色 ID 二选一）"
              style={{ marginBottom: 8 }}
            >
              <Input placeholder="用户 UUID（可选）" />
            </Form.Item>
            <Form.Item
              name="role_id"
              label="角色 ID（与用户 ID 二选一）"
              style={{ marginBottom: 8 }}
            >
              <Input placeholder="角色 UUID（可选）" />
            </Form.Item>
          </Space>
          <Form.Item
            name="resource_type"
            label="资源类型"
            rules={[{ required: true, message: '请输入资源类型' }]}
          >
            <Input placeholder='资源类型（如 fact、artifact）或通配符 *' />
          </Form.Item>
          <Form.Item
            name="action"
            label="权限操作"
            rules={[{ required: true, message: '请输入权限操作' }]}
          >
            <Input placeholder="权限字符串（如 fact:read）" />
          </Form.Item>
          <Form.Item name="object_root_id" label="对象根 ID（可选）">
            <Input placeholder="对象根 UUID，留空表示全组织" />
          </Form.Item>
          <Form.Item name="department_id" label="部门 ID（可选）">
            <Input placeholder="部门 UUID，留空表示全组织" />
          </Form.Item>
          <Text type="secondary" style={{ fontSize: 12 }}>
            user_id 和 role_id 必须二选一，不能同时指定。
          </Text>
        </Form>
      </Modal>
    </div>
  );
}
