import { useState } from 'react';
import {
  Alert,
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
  TreeSelect,
  Typography,
  message,
} from 'antd';
import { PlusOutlined, LockOutlined } from '@ant-design/icons';

const { Text } = Typography;
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateDepartment,
  apiDeleteDepartment,
  apiGetReparentImpact,
  apiListDepartments,
  apiUpdateDepartment,
  apiUpdateDepartmentStatus,
  isSentinelDept,
  type DepartmentListItem,
  type ReparentImpactResponse,
} from '@/api/departments';
// P2-C22: 辅助函数提取到 departmentUtils.ts
import {
  buildTree,
  getDescendantIds,
  extractErrorMessage,
  type DepartmentTreeNode,
} from '@/features/governance/departmentUtils';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { MemberDrawer } from '@/features/governance/MemberDrawer';
import { EquipmentPage } from '@/features/equipment/EquipmentPage';
import { QueryStateDisplay } from '@/features/components/StateDisplay';

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
 *
 * P2-C22: buildTree/getDescendantIds/extractErrorMessage 已提取到 departmentUtils.ts
 */

export function DepartmentManagement(): JSX.Element {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.roles?.includes('platform_administrator') ?? false;
  const isLabDirector = user?.roles?.includes('lab_director') ?? false;
  const userDeptId = user?.departmentId;

  /** lab_director 只能管理主部门及子部门；platform_administrator 可管全部 */
  const canManageDept = (deptId: string): boolean => {
    if (isAdmin) return true;
    if (!isLabDirector || !userDeptId) return false;
    // 主部门自身
    if (deptId === userDeptId) return true;
    // 主部门的子部门
    return getDescendantIds(items, userDeptId).has(deptId);
  };
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  // 仪器抽屉：就地新建设备仪器，不跳转页面
  const [equipDrawerOpen, setEquipDrawerOpen] = useState(false);
  const [equipDrawerDeptId, setEquipDrawerDeptId] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingDept, setEditingDept] = useState<DepartmentListItem | null>(null);
  const [memberDrawerDept, setMemberDrawerDept] = useState<DepartmentListItem | null>(null);
  const [reparentTarget, setReparentTarget] = useState<DepartmentListItem | null>(null);
  const [reparentNewParent, setReparentNewParent] = useState<string | null>(null);
  const [reparentImpact, setReparentImpact] = useState<ReparentImpactResponse | null>(null);
  const [reparentConfirmOpen, setReparentConfirmOpen] = useState(false);
  const [reparentLoading, setReparentLoading] = useState(false);
  const [form] = Form.useForm();

  // ---- 数据查询 ----
  const { data, isLoading, isError, error, refetch } = useQuery({
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
      const msg = extractErrorMessage(err);
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
      const msg = extractErrorMessage(err);
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
      const msg = extractErrorMessage(err);
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
      const msg = extractErrorMessage(err);
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
    const { apiGetDepartment } = await import('@/api/departments');
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
        const { apiGetDepartment } = await import('@/api/departments');
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
      const { apiGetDepartment } = await import('@/api/departments');
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

  // ---- re-parent 移动操作 ----
  const handleReparentFetchImpact = async (): Promise<void> => {
    if (!reparentTarget) return;
    setReparentLoading(true);
    try {
      const impact = await apiGetReparentImpact(reparentTarget.id, reparentNewParent);
      setReparentImpact(impact);
    } catch (err: unknown) {
      message.error(extractErrorMessage(err));
    } finally {
      setReparentLoading(false);
    }
  };

  const handleReparentConfirm = async (): Promise<void> => {
    if (!reparentTarget || !reparentNewParent) return;
    try {
      const { apiGetDepartment } = await import('@/api/departments');
      const detail = await apiGetDepartment(reparentTarget.id);
      updateMutation.mutate({
        id: reparentTarget.id,
        body: {
          display_name: reparentTarget.display_name,
          description: detail.description ?? undefined,
          sort_order: reparentTarget.sort_order,
          lock_version: detail.lock_version,
          parent_id: reparentNewParent ?? undefined,
        },
      });
      setReparentConfirmOpen(false);
      setReparentTarget(null);
      setReparentNewParent(null);
      setReparentImpact(null);
    } catch (err: unknown) {
      message.error(extractErrorMessage(err));
    }
  };

  // ---- re-parent 选项（排除自身及子孙防成环） ----
  const reparentExcludeIds = reparentTarget
    ? getDescendantIds(items, reparentTarget.id)
    : new Set<string>();
  const reparentTreeData = items
    .filter((item) => !reparentExcludeIds.has(item.id) && !isSentinelDept(item.code))
    .map((item) => ({
      value: item.id,
      title: item.display_name,
    }));

  // ---- 表格列定义 ----
  const columns: ColumnsType<DepartmentTreeNode> = [
    {
      title: '名称',
      dataIndex: 'display_name',
      key: 'display_name',
      width: 280,
      render: (name: string, record: DepartmentTreeNode) => (
        <Tooltip title={record.description || undefined} placement="topLeft">
          <Space>
            {isSentinelDept(record.code) && (
              <LockOutlined style={{ color: '#999', fontSize: 12 }} />
            )}
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
            disabled={!canManageDept(record.id) || isSentinelDept(record.code)}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => {
              setEquipDrawerDeptId(record.id);
              setEquipDrawerOpen(true);
            }}
            disabled={!canManageDept(record.id)}
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
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate} disabled={!isAdmin}>
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

      <QueryStateDisplay
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={!isLoading && !isError && treeData.length === 0}
        emptyText="暂无实验室数据"
        onRetry={() => void refetch()}
        loadingTitle="加载实验室列表…"
      >
        <Table<DepartmentTreeNode>
          columns={columns}
          dataSource={treeData}
          rowKey="id"
          pagination={false}
          size="middle"
          scroll={{ y: 600 }}
          expandable={{
            childrenColumnName: 'children',
            defaultExpandAllRows: true,
          }}
        />
      </QueryStateDisplay>

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

      {/* 新建设备仪器抽屉：就地操作，不跳转页面 */}
      <Drawer
        title="新建设备仪器"
        open={equipDrawerOpen}
        onClose={() => {
          setEquipDrawerOpen(false);
          setEquipDrawerDeptId(undefined);
          void queryClient.invalidateQueries({ queryKey: ['equipment'] });
        }}
        width={960}
        destroyOnClose
      >
        <EquipmentPage
          presetDeptId={equipDrawerDeptId}
          onPresetDeptIdConsumed={() => setEquipDrawerDeptId(undefined)}
        />
      </Drawer>

      {/* re-parent 移动确认对话框（阶段2新增） */}
      <Modal
        title="移动部门"
        open={reparentConfirmOpen}
        onCancel={() => {
          setReparentConfirmOpen(false);
          setReparentTarget(null);
          setReparentNewParent(null);
          setReparentImpact(null);
        }}
        footer={
          <Space>
            <Button onClick={() => {
              setReparentConfirmOpen(false);
              setReparentTarget(null);
              setReparentNewParent(null);
              setReparentImpact(null);
            }}>
              取消
            </Button>
            <Button
              onClick={handleReparentFetchImpact}
              disabled={!reparentNewParent}
              loading={reparentLoading}
            >
              预览影响
            </Button>
            <Button
              type="primary"
              onClick={handleReparentConfirm}
              disabled={!reparentNewParent || !reparentImpact}
              loading={updateMutation.isPending}
            >
              确认移动
            </Button>
          </Space>
        }
      >
        {reparentTarget && (
          <div style={{ marginBottom: 16 }}>
            <Text>将「{reparentTarget.display_name}」移动到：</Text>
          </div>
        )}
        <TreeSelect
          value={reparentNewParent ?? undefined}
          onChange={(val: string | null) => {
            setReparentNewParent(val);
            setReparentImpact(null);
          }}
          placeholder="选择目标父部门"
          style={{ width: '100%', marginBottom: 16 }}
          treeData={reparentTreeData.map((item) => ({
            ...item,
            selectable: true,
          }))}
          treeDefaultExpandAll
          showSearch
          treeNodeFilterProp="title"
          allowClear
        />
        {reparentImpact && (
          <Alert
            type="info"
            showIcon
            message="影响预览"
            description={
              <div>
                <div>子树部门数（含自身）：{reparentImpact.subtree_count}</div>
                <div>关联设备数：{reparentImpact.equipment_count}</div>
              </div>
            }
          />
        )}
      </Modal>
    </div>
  );
}

