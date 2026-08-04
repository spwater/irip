import { useEffect, useMemo, useState } from 'react';
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  TreeSelect,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';

const { Text } = Typography;
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateObject,
  apiDeleteObject,
  apiGetObject,
  apiListObjects,
  apiListObjectTypes,
  apiCreateObjectType,
  apiUpdateObjectType,
  apiDeleteObjectType,
  apiUpdateObject,
  apiUpdateObjectStatus,
  type ObjectTypeDictItem,
} from '@/api/standards-objects';
import { apiGetDepartmentNameMap, apiListDepartments } from '@/api/departments';
import { buildDeptTree } from '@/shared/buildDeptTree';
import { apiListEquipment, apiListComponents, apiPublishComponent } from '@/api/equipment-flows';
import { extractApiError, type IndustrialObject } from '@/api/types';
import { apiListIngestionTools } from '@/api/models-ai';
import { buildManifestYaml, FORM_FIELD_NAMES, FRESH_FORM_VALUES } from '@/shared/component-utils';
import { ComponentFormFields } from '@/features/components/ComponentFormFields';

/**
 * 实验对象管理页面（要素管理第 3 个 Tab）
 *
 * 实验对象 = industrial_object 表中 object_type 为 material / signal 的记录。
 * - material：物料
 * - signal：信号
 *
 * 功能：
 * - Ant Design Table 列表（编码 / 名称 / 类型 / 关联设备 / 状态 / 描述 / 操作）
 * - 顶部"新建实验对象"按钮 + 类型筛选
 * - Modal + Form 创建/编辑弹窗（编码与类型创建后锁定）
 * - Popconfirm 启用/禁用确认
 */

/** 实验对象类型选项（用于筛选下拉） */
const EXP_OBJECT_TYPES = [
  { value: '__all__', label: '全部' },
  { value: 'material', label: '物料' },
  { value: 'signal', label: '信号' },
];

/** 类型 → 中文标签 */
const TYPE_LABEL: Record<string, string> = {
  material: '物料',
  signal: '信号',
};

/** 列表查询用的类型过滤 */
const LIST_TYPE_FILTER = 'material,signal';

// 引用以消除未使用警告（常量供后续扩展使用）
void EXP_OBJECT_TYPES;
void TYPE_LABEL;
void LIST_TYPE_FILTER;

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

export function ExperimentalObjectPage({
  presetEquipmentId,
  onPresetConsumed,
}: {
  presetEquipmentId?: string;
  onPresetConsumed?: () => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<IndustrialObject | null>(null);
  const [typeMgrOpen, setTypeMgrOpen] = useState(false);
  const [newTypeName, setNewTypeName] = useState('');
  const [newTypeDesc, setNewTypeDesc] = useState('');
  const [editingType, setEditingType] = useState<ObjectTypeDictItem | null>(null);
  const [editTypeName, setEditTypeName] = useState('');
  const [editTypeDesc, setEditTypeDesc] = useState('');
  const [form] = Form.useForm();
  const [compCreateModalOpen, setCompCreateModalOpen] = useState(false);
  const [compForm] = Form.useForm();
  const [compAdvancedMode, setCompAdvancedMode] = useState(false);

  // 动态加载类型字典
  const { data: objectTypeData } = useQuery({
    queryKey: ['object-types'],
    queryFn: apiListObjectTypes,
  });
  const objectTypeOptions = (objectTypeData ?? []).map((t) => ({
    value: t.code,
    label: t.display_name,
  }));
  const objectTypeMap = new Map(
    (objectTypeData ?? []).map((t) => [t.code, t.display_name]),
  );
  // 引用以消除未使用警告
  void objectTypeMap;

  // 当 presetEquipmentId 变化时，自动打开新建弹窗并预填
  useEffect(() => {
    if (presetEquipmentId) {
      setEditingItem(null);
      form.resetFields();
      setModalOpen(true);
      onPresetConsumed?.();
    }
  }, [presetEquipmentId]);

  // ---- 数据查询：始终拿全部类型的数据，前端按 typeFilter 过滤 ----
  const allTypeCodes = (objectTypeData ?? []).map(t => t.code).join(',') || 'material,signal';
  const { data, isLoading } = useQuery({
    queryKey: ['exp-objects', allTypeCodes],
    queryFn: () =>
      apiListObjects({
        object_type: allTypeCodes,
        page_size: 100,
      }),
  });

  const items: IndustrialObject[] = data?.items ?? [];

  // 全部门名称映射（不受部门隔离限制），用于所属单位/可见单位列名称展示
  const { data: deptNameMapData } = useQuery({
    queryKey: ['department-name-map'],
    queryFn: apiGetDepartmentNameMap,
  });
  const deptMap = new Map(
    (deptNameMapData ?? []).map((d) => [d.id, d.display_name]),
  );

  const { data: deptData } = useQuery({
    queryKey: ['departments-for-object-filter'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptOptions = (deptData?.items ?? []).map((d) => ({
    value: d.id,
    label: d.display_name,
  }));

  // ---- 设备列表查询（用于接口行的关联设备列）----
  const { data: equipmentData } = useQuery({
    queryKey: ['equipment-for-object-tree'],
    queryFn: () => apiListEquipment({ limit: 100 }),
  });
  const equipmentMap = new Map(
    (equipmentData?.items ?? []).map((e) => [e.id, e.display_name]),
  );

  // ---- 数据接口列表查询（用于下拉选择与列表展示）----
  const { data: componentData } = useQuery({
    queryKey: ['components'],
    queryFn: () => apiListComponents(),
  });
  const componentOptions = (componentData?.items ?? []).map((c) => ({
    value: c.id,
    label: c.display_name || c.name,
  }));
  const componentMap = new Map(
    (componentData?.items ?? []).map((c) => [c.id, c.display_name || c.name]),
  );

  // ---- 解析工具列表（用于新建接口表单）----
  const { data: ingestionToolsData } = useQuery({
    queryKey: ['ingestion-tools'],
    queryFn: apiListIngestionTools,
  });
  const ingestionToolOptions = (ingestionToolsData ?? []).map((t) => ({
    value: t.name,
    label: t.display_name,
  }));

  // ---- 筛选逻辑 ----
  let filteredItems = items;
  if (deptFilter) {
    filteredItems = items.filter((o) => o.department_id === deptFilter);
  }

  // 全部部门选项（用于所属单位 + 可见单位选择）
  const allDeptOptions = (deptData?.items ?? []).map((d) => ({
    value: d.id,
    label: d.display_name,
  }));

  // 监听表单中的 department_id
  const watchedDeptId = Form.useWatch('department_id', form);

  // 部门树数据（用于可见单位树形多选）
  const deptTreeData = useMemo(
    () => buildDeptTree(deptData?.items ?? []),
    [deptData],
  );

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateObject,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      void queryClient.invalidateQueries({ queryKey: ['objects'] });
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
      body: {
        display_name: string;
        description?: string | null;
        object_type?: string;
        department_id?: string | null;
        visible_departments?: string[] | null;
        component_id?: string | null;
      };
    }) => apiUpdateObject(params.id, params.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      void queryClient.invalidateQueries({ queryKey: ['objects'] });
      setModalOpen(false);
      setEditingItem(null);
      form.resetFields();
      message.success('实验对象更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 新建数据接口 Mutation ----
  const publishCompMutation = useMutation({
    mutationFn: apiPublishComponent,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      setCompCreateModalOpen(false);
      compForm.resetFields();
      message.success('数据接口创建成功');
      // 自动选中新建的接口
      form.setFieldsValue({ component_id: data.id });
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
      void queryClient.invalidateQueries({ queryKey: ['objects'] });
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
      void queryClient.invalidateQueries({ queryKey: ['objects'] });
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
    form.setFieldsValue({ visible_departments: [], component_id: undefined });
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
      department_id: detail.department_id ?? undefined,
      visible_departments: detail.visible_departments ?? [],
      component_id: detail.component_id ?? undefined,
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
            object_type: values.object_type,
            department_id: values.department_id,
            visible_departments: values.visible_departments ?? [],
            component_id: values.component_id ?? null,
          },
        });
      } else {
        createMutation.mutate({
          display_name: values.display_name,
          object_type: values.object_type,
          description: values.description,
          department_id: values.department_id,
          visible_departments: values.visible_departments ?? [],
          component_id: values.component_id || undefined,
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

  // ---- 构建树形数据：第一层是类型，第二层是对象，第三层是接口 ----
  type TreeRow = IndustrialObject & { children?: TreeRow[] };
  const treeData: TreeRow[] = (() => {
    // 按 object_type 分组
    const typeMap = new Map<string, IndustrialObject[]>();
    for (const item of filteredItems) {
      const list = typeMap.get(item.object_type) ?? [];
      list.push(item);
      typeMap.set(item.object_type, list);
    }
    const tree: TreeRow[] = [];
    for (const typeItem of objectTypeData ?? []) {
      if (typeFilter && typeItem.code !== typeFilter) continue;
      const objs = typeMap.get(typeItem.code);
      if (objs && objs.length > 0) {
        // 对象行（不再挂载接口子行）
        const objRows: TreeRow[] = objs.map((obj) => ({ ...obj }) as TreeRow);
        tree.push({
          id: `type_${typeItem.code}`,
          code: typeItem.code,
          display_name: typeItem.display_name,
          object_type: typeItem.code,
          description: typeItem.description,
          status: '',
          department_id: null,
          equipment_id: null,
          component_id: null,
          visible_departments: [],
          visibility_scope: 'tree',
          owner_user_id: null,
          created_at: '',
          updated_at: '',
          lock_version: 0,
          children: objRows,
        } as TreeRow);
        typeMap.delete(typeItem.code);
      }
    }
    if (!typeFilter) {
      for (const [, objs] of typeMap) {
        for (const obj of objs) {
          tree.push({ ...obj } as TreeRow);
        }
      }
    }
    return tree;
  })();

  const isTypeRow = (record: TreeRow): boolean => record.id.startsWith('type_');
  const isObjectRow = (record: TreeRow): boolean => !record.id.toString().startsWith('type_'); // eslint-disable-line @typescript-eslint/no-unused-vars

  // ---- 表格列定义 ----
  const columns: ColumnsType<TreeRow> = [
    {
      title: '名称',
      key: 'name',
      width: 333,
      render: (_: unknown, record: TreeRow) => {
        if (isTypeRow(record)) {
          return (
            <Space size={6}>
              <Text strong style={{ fontSize: 14 }}>{record.display_name}</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {record.code}
              </Text>
            </Space>
          );
        }
        return (
          <Tooltip title={record.description || undefined} placement="topLeft">
            <Space size={6}>
              <Text strong>{record.display_name}</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {record.code}
              </Text>
            </Space>
          </Tooltip>
        );
      },
    },
    {
      title: '所属单位',
      dataIndex: 'department_id',
      key: 'department_id',
      width: 140,
      render: (deptId: string | null, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        const name = deptId ? deptMap.get(deptId) : null;
        return name ? <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{name}</Tag> : <Text type="secondary">-</Text>;
      },
    },
    {
      title: '可见单位',
      dataIndex: 'visible_departments',
      key: 'visible_departments',
      width: 300,
      render: (deptIds: string[] | null, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        if (!deptIds || deptIds.length === 0) {
          return <Text type="secondary">-</Text>;
        }
        return (
          <Space size="small" wrap>
            {deptIds.map((id) => {
              const name = deptMap.get(id);
              return name ? <Tag key={id} color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{name}</Tag> : null;
            })}
          </Space>
        );
      },
    },
    {
      title: '数据接口',
      dataIndex: 'component_id',
      key: 'component_id',
      width: 150,
      render: (compId: string | null, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        if (!compId) return <Text type="secondary">-</Text>;
        const comp = componentMap.get(compId);
        return comp ? <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{comp}</Tag> : <Text type="secondary">-</Text>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (s: string, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        return (
          <Tag color={STATUS_COLOR[s] ?? 'default'}>
            {STATUS_LABEL[s] ?? s}
          </Tag>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: unknown, record: TreeRow) => {
        if (isTypeRow(record)) return null;
        return (
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
        );
      },
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          新建实验对象
        </Button>
        <Button onClick={() => setTypeMgrOpen(true)}>
          类型管理
        </Button>
        <Select
          placeholder="类型筛选"
          style={{ width: 140 }}
          value={typeFilter ?? '__all__'}
          onChange={(val: string) => setTypeFilter(val === '__all__' ? undefined : val)}
          options={[{ value: '__all__', label: '全部' }, ...objectTypeOptions]}
        />
        <Select
          placeholder="关联单位筛选"
          style={{ width: 160 }}
          value={deptFilter ?? '__all__'}
          onChange={(val: string) => {
            const v = val === '__all__' ? undefined : val;
            setDeptFilter(v);
          }}
          options={[{ value: '__all__', label: '全部' }, ...deptOptions]}
        />
      </Space>

      <Table<TreeRow>
        columns={columns}
        dataSource={treeData}
        rowKey="id"
        loading={isLoading}
        pagination={false}
        size="middle"
        expandable={{ defaultExpandAllRows: false }}
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
          >
            <Select
              placeholder="选择实验对象类型"
              options={objectTypeOptions}
            />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea
              placeholder="对象描述（可选）"
              rows={3}
              maxLength={2000}
            />
          </Form.Item>
          <Form.Item name="department_id" label="所属单位" rules={[{ required: true, message: '请选择所属单位' }]}>
            <Select
              placeholder="选择所属单位"
              showSearch
              optionFilterProp="label"
              options={allDeptOptions}
            />
          </Form.Item>
          <Form.Item
            name="visible_departments"
            label="可见单位"
            tooltip="选填。默认按部门层级可见（上级可看下级、下级可看上级）。如需对其他部门可见，请在此添加。"
          >
            <TreeSelect
              treeData={deptTreeData}
              treeCheckable
              treeDefaultExpandAll
              showSearch
              treeNodeFilterProp="title"
              placeholder="不选则按部门层级默认可见"
              allowClear
              style={{ width: '100%' }}
              maxTagCount={5}
            />
          </Form.Item>
          <Form.Item label="数据接口">
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="component_id" noStyle>
                <Select
                  placeholder="选择数据接口（可选）"
                  showSearch
                  optionFilterProp="label"
                  options={componentOptions}
                  allowClear
                  style={{ width: 'calc(100% - 40px)' }}
                />
              </Form.Item>
              <Button
                icon={<PlusOutlined />}
                onClick={() => {
                  const curDept = form.getFieldValue('department_id') as string | undefined;
                  const curName = form.getFieldValue('display_name') as string | undefined;
                  compForm.resetFields();
                  setCompAdvancedMode(false);
                  compForm.setFieldsValue({
                    ...FRESH_FORM_VALUES,
                    tool_type: 'llm_converter',
                    department_id: curDept,
                    display_name: curName ? `${curName}接口` : undefined,
                  });
                  setCompCreateModalOpen(true);
                }}
                title="新建数据接口"
                style={{ width: 40 }}
              />
            </Space.Compact>
          </Form.Item>
        </Form>
      </Modal>

      {/* 类型管理弹窗 */}
      <Modal
        title="类型管理"
        open={typeMgrOpen}
        onCancel={() => { setTypeMgrOpen(false); setNewTypeName(''); setNewTypeDesc(''); setEditingType(null); }}
        footer={null}
        width={650}
      >
        {/* 新建类型 */}
        <div style={{ marginBottom: 16 }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              placeholder="新类型名称"
              value={newTypeName}
              onChange={(e) => setNewTypeName(e.target.value)}
              maxLength={100}
            />
            <Button
              type="primary"
              onClick={async () => {
                if (!newTypeName.trim()) return;
                try {
                  await apiCreateObjectType({ display_name: newTypeName.trim(), description: newTypeDesc || undefined });
                  void queryClient.invalidateQueries({ queryKey: ['object-types'] });
                  setNewTypeName('');
                  setNewTypeDesc('');
                  message.success('类型创建成功');
                } catch (err) {
                  message.error(extractApiError(err));
                }
              }}
            >
              新建
            </Button>
          </Space.Compact>
          <Input
            placeholder="描述（可选）"
            value={newTypeDesc}
            onChange={(e) => setNewTypeDesc(e.target.value)}
            maxLength={500}
            style={{ marginTop: 8 }}
          />
        </div>

        {/* 类型列表 */}
        <ObjectTypesList
          onEdit={(item) => {
            setEditingType(item);
            setEditTypeName(item.display_name);
            setEditTypeDesc(item.description ?? '');
          }}
          onDelete={async (item) => {
            try {
              await apiDeleteObjectType(item.id);
              void queryClient.invalidateQueries({ queryKey: ['object-types'] });
              message.success('类型已删除');
            } catch (err) {
              message.error(extractApiError(err));
            }
          }}
        />

        {/* 编辑类型 */}
        {editingType && (
          <div style={{ marginTop: 16, padding: 12, background: 'var(--ocean-surface-structural)', borderRadius: 8 }}>
            <Text strong>编辑类型: {editingType.code}</Text>
            <Input
              placeholder="类型名称"
              value={editTypeName}
              onChange={(e) => setEditTypeName(e.target.value)}
              maxLength={100}
              style={{ marginTop: 8 }}
            />
            <Input
              placeholder="描述"
              value={editTypeDesc}
              onChange={(e) => setEditTypeDesc(e.target.value)}
              maxLength={500}
              style={{ marginTop: 8 }}
            />
            <Space style={{ marginTop: 8 }}>
              <Button
                type="primary"
                size="small"
                onClick={async () => {
                  try {
                    await apiUpdateObjectType(editingType.id, {
                      display_name: editTypeName,
                      description: editTypeDesc || undefined,
                    });
                    void queryClient.invalidateQueries({ queryKey: ['object-types'] });
                    setEditingType(null);
                    message.success('类型已更新');
                  } catch (err) {
                    message.error(extractApiError(err));
                  }
                }}
              >
                保存
              </Button>
              <Button size="small" onClick={() => setEditingType(null)}>
                取消
              </Button>
            </Space>
          </div>
        )}
      </Modal>

      {/* 新建数据接口弹窗（完整表单，复用 ComponentFormFields） */}
      <Modal
        title="新建数据接口"
        open={compCreateModalOpen}
        onOk={async () => {
          try {
            if (compAdvancedMode) {
              const values = await compForm.validateFields(['manifest_yaml', 'department_id', 'visible_departments']);
              publishCompMutation.mutate({
                manifest_yaml: values.manifest_yaml as string,
                department_id: (values.department_id as string) ?? null,
                visible_departments: (values.visible_departments as string[] | undefined) ?? null,
              });
            } else {
              const values = await compForm.validateFields([...FORM_FIELD_NAMES, 'department_id', 'visible_departments']);
              const yaml = buildManifestYaml({
                display_name: values.display_name as string,
                description: (values.description as string) ?? '',
                prompt: (values.prompt as string) ?? '',
                tool_type: (values.tool_type as string) ?? 'llm_converter',
              });
              publishCompMutation.mutate({
                manifest_yaml: yaml,
                department_id: (values.department_id as string) ?? null,
                visible_departments: (values.visible_departments as string[] | undefined) ?? null,
              });
            }
          } catch {
            // validation error
          }
        }}
        onCancel={() => {
          setCompCreateModalOpen(false);
          compForm.resetFields();
          setCompAdvancedMode(false);
        }}
        confirmLoading={publishCompMutation.isPending}
        okText="发布"
        cancelText="取消"
        width={680}
        destroyOnClose
      >
        <Form form={compForm} layout="vertical">
          <div style={{ marginBottom: 16 }}>
            <Space align="center">
              <Text>高级模式</Text>
              <Switch
                checked={compAdvancedMode}
                onChange={(checked) => {
                  if (checked) {
                    const vals = compForm.getFieldsValue();
                    const yaml = buildManifestYaml({
                      display_name: vals.display_name ?? '',
                      description: vals.description ?? '',
                      prompt: vals.prompt ?? '',
                      tool_type: vals.tool_type ?? 'llm_converter',
                    });
                    compForm.setFieldsValue({ manifest_yaml: yaml });
                  }
                  setCompAdvancedMode(checked);
                }}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                {compAdvancedMode ? '直接编辑 YAML 全文' : '填写表单字段，自动生成 YAML'}
              </Text>
            </Space>
          </div>
          {compAdvancedMode ? (
            <Form.Item
              name="manifest_yaml"
              label="接口清单 (YAML)"
              rules={[{ required: true, message: '请输入 YAML' }, { min: 10, message: '清单内容过短' }]}
            >
              <Input.TextArea
                placeholder={'name: iface_ffffffff  # 自动生成\nkind: ingestion\ndisplay_name: "接口名"\n...'}
                rows={16}
                style={{ fontFamily: 'monospace', fontSize: 13 }}
              />
            </Form.Item>
          ) : (
            <ComponentFormFields ingestionToolOptions={ingestionToolOptions} />
          )}
          <Form.Item name="department_id" label="所属单位">
            <Select
              placeholder="选择所属单位（可选）"
              allowClear
              showSearch
              optionFilterProp="label"
              options={deptOptions}
            />
          </Form.Item>
          <Form.Item
            name="visible_departments"
            label="可见单位"
            tooltip="选填。默认按部门层级可见。如需对其他部门可见，请在此添加。"
          >
            <TreeSelect
              treeData={deptTreeData}
              treeCheckable
              treeDefaultExpandAll
              showSearch
              treeNodeFilterProp="title"
              placeholder="不选则按部门层级默认可见"
              allowClear
              style={{ width: '100%' }}
              maxTagCount={5}
            />
          </Form.Item>
        </Form>
      </Modal>

    </div>
  );
}

/** 类型管理列表子组件 */
function ObjectTypesList({
  onEdit,
  onDelete,
}: {
  onEdit: (item: ObjectTypeDictItem) => void;
  onDelete: (item: ObjectTypeDictItem) => void;
}): JSX.Element {
  const { data, isLoading } = useQuery({
    queryKey: ['object-types'],
    queryFn: apiListObjectTypes,
  });
  const items = data ?? [];
  if (isLoading) return <Spin />;
  if (items.length === 0) return <Text type="secondary">暂无类型</Text>;
  return (
    <Table<ObjectTypeDictItem>
      dataSource={items}
      rowKey="id"
      size="small"
      pagination={false}
      columns={[
        { title: '名称', dataIndex: 'display_name', key: 'display_name', width: 120 },
        { title: '编码', dataIndex: 'code', key: 'code', width: 140 },
        { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
        {
          title: '操作',
          key: 'action',
          width: 120,
          render: (_: unknown, record: ObjectTypeDictItem) => (
            <Space size="small">
              <Button type="link" size="small" onClick={() => onEdit(record)}>
                改名
              </Button>
              <Popconfirm
                title="确定删除该类型？"
                description="如果类型下有对象则无法删除"
                onConfirm={() => onDelete(record)}
                okText="确定"
                cancelText="取消"
              >
                <Button type="link" size="small" danger>
                  删除
                </Button>
              </Popconfirm>
            </Space>
          ),
        },
      ]}
    />
  );
}
