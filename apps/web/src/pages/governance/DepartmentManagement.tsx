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
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateDepartment,
  apiListDepartments,
  apiUpdateDepartment,
  apiUpdateDepartmentStatus,
  type DepartmentListItem,
} from '@/api/client';
import { MemberDrawer } from '@/pages/governance/MemberDrawer';

/**
 * 实验室管理组件（P0）
 *
 * 功能：
 * - Ant Design Table 列表（编码 / 名称 / 状态 / 成员数 / 操作）
 * - 按 sort_order + created_at 排序
 * - 顶部"新建实验室"按钮 + 状态筛选 Select
 * - Modal + Form 创建/编辑弹窗（code 编辑时 disabled）
 * - Popconfirm 启用/禁用确认
 * - 禁用行灰色标签
 * - 成员管理抽屉（P1，MemberDrawer）
 */
export function DepartmentManagement(): JSX.Element {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingDept, setEditingDept] = useState<DepartmentListItem | null>(null);
  const [memberDrawerDept, setMemberDrawerDept] = useState<DepartmentListItem | null>(null);
  const [form] = Form.useForm();

  // ---- 数据查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['departments', statusFilter],
    queryFn: () => apiListDepartments({ status: statusFilter }),
  });

  const items: DepartmentListItem[] = data?.items ?? [];

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateDepartment,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
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
      body: { display_name: string; description?: string; sort_order: number; lock_version: number };
    }) => apiUpdateDepartment(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
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
      message.success('状态更新成功');
    },
    onError: (err: unknown) => {
      const msg = _extractErrorMessage(err);
      message.error(msg);
    },
  });

  // ---- 事件处理 ----

  const handleCreate = (): void => {
    setEditingDept(null);
    form.resetFields();
    form.setFieldsValue({ sort_order: 0 });
    setModalOpen(true);
  };

  const handleEdit = async (record: DepartmentListItem): Promise<void> => {
    // 获取详情以拿到 lock_version + description（列表项不含这两个字段）
    const { apiGetDepartment } = await import('@/api/client');
    const detail = await apiGetDepartment(record.id);
    setEditingDept(record);
    form.setFieldsValue({
      code: record.code,
      display_name: record.display_name,
      description: detail.description ?? '',
      sort_order: record.sort_order,
    });
    setModalOpen(true);
  };

  const handleSubmit = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      if (editingDept) {
        // 编辑：需要 lock_version，从列表项无法获取，需先查询详情
        // 这里通过详情接口获取完整数据
        const { apiGetDepartment } = await import('@/api/client');
        const detail = await apiGetDepartment(editingDept.id);
        updateMutation.mutate({
          id: editingDept.id,
          body: {
            display_name: values.display_name,
            description: values.description ?? detail.description ?? null,
            sort_order: values.sort_order ?? 0,
            lock_version: detail.lock_version,
          },
        });
      } else {
        // 创建
        createMutation.mutate({
          code: values.code,
          display_name: values.display_name,
          description: values.description ?? null,
          sort_order: values.sort_order ?? 0,
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

  // ---- 表格列定义 ----
  const columns: ColumnsType<DepartmentListItem> = [
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
      title: '排序',
      dataIndex: 'sort_order',
      key: 'sort_order',
      width: 80,
      align: 'center',
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_: unknown, record: DepartmentListItem) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => handleEdit(record)}
          >
            编辑
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
        <Button type="primary" onClick={handleCreate}>
          新建实验室
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
      </Space>

      <Table<DepartmentListItem>
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={isLoading}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        size="middle"
      />

      <Modal
        title={editingDept ? '编辑实验室' : '新建实验室'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => {
          setModalOpen(false);
          setEditingDept(null);
          form.resetFields();
        }}
        confirmLoading={createMutation.isPending || updateMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="code"
            label="实验室编码"
            rules={[
              { required: true, message: '请输入实验室编码' },
              {
                pattern: /^[a-z][a-z0-9_]*$/,
                message: '仅小写字母/数字/下划线，首字符必须为字母',
              },
            ]}
            extra={editingDept ? '编码创建后锁定，不可修改' : undefined}
          >
            <Input
              placeholder="如：lab_materials"
              disabled={!!editingDept}
              maxLength={64}
            />
          </Form.Item>
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
            name="sort_order"
            label="排序权重"
            rules={[{ required: true, message: '请输入排序权重' }]}
          >
            <InputNumber min={0} style={{ width: '100%' }} />
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
