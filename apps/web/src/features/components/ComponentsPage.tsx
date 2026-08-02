/**
 * 数据接口页面（ComponentsPage）
 *
 * 从原始 1432 行拆分为 3 个模块：
 * - component-utils.ts: 常量 + 工具函数（YAML 构建/解析、版本比较等）
 * - ComponentFormFields.tsx: 表单字段组件（级联选择器+文件预加载+预览）
 * - ComponentDetailPanel.tsx: 详情面板（基本信息+YAML预览+版本历史+回滚）
 * - ComponentsPage.tsx: 主页面（列表+筛选+Modal+Drawer编排）
 */

import { useEffect, useState } from 'react';
import {
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiArchiveComponent,
  apiDeleteComponent,
  apiGetComponent,
  apiListComponents,
  apiListEquipment,
  apiPublishComponent,
  apiRestoreComponent,
  type ComponentSummary,
} from '@/api/equipment-flows';
import { apiListObjects, apiListObjectTypes } from '@/api/standards-objects';
import { apiListDepartments } from '@/api/departments';
import { apiListIngestionTools } from '@/api/models-ai';
import { extractApiError, type IndustrialObject } from '@/api/types';
import { DepartmentSelector } from '@/shared/DepartmentSelector';
import { useAuthStore } from '@/features/auth/AuthProvider';
import {
  buildManifestYaml,
  compareVersions,
  FRESH_FORM_VALUES,
  FORM_FIELD_NAMES,
  parseYamlToFormValues,
  STATUS_COLOR,
  STATUS_LABEL,
} from '@/shared/component-utils';
import { ComponentFormFields } from './ComponentFormFields';
import { ComponentDetailPanel } from './ComponentDetailPanel';

const { Text } = Typography;

export function ComponentsPage({ prefillObject, editId, hideList }: { prefillObject?: string; editId?: string; hideList?: boolean }): JSX.Element {
  const queryClient = useQueryClient();
  const currentUser = useAuthStore((s) => s.user);
  const [activeTab, setActiveTab] = useState<'modern' | 'archived'>('modern');
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [equipmentFilter, setEquipmentFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editOriginalName, setEditOriginalName] = useState<string | undefined>(undefined);
  const [advancedMode, setAdvancedMode] = useState(false);
  const [editAdvancedMode, setEditAdvancedMode] = useState(true);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [detailId, setDetailId] = useState<string | null>(null);

  useEffect(() => {
    if (prefillObject) {
      form.resetFields();
      setAdvancedMode(false);
      setModalOpen(true);
      form.setFieldsValue({ experimental_object_code: prefillObject });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillObject]);

  useEffect(() => {
    if (editId) {
      setDetailId(editId);
      void handleOpenEdit({ id: editId } as ComponentSummary);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editId]);

  // ---- 列表查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['components'],
    queryFn: () => apiListComponents(),
  });

  const { data: objectData } = useQuery({
    queryKey: ['objects-for-component'],
    queryFn: () => apiListObjects({ page_size: 100 }),
    staleTime: 0,
    refetchOnMount: true,
  });
  const { data: objectTypeData } = useQuery({
    queryKey: ['object-types'],
    queryFn: apiListObjectTypes,
  });
  const { data: ingestionToolsData } = useQuery({
    queryKey: ['ingestion-tools'],
    queryFn: apiListIngestionTools,
  });
  const ingestionToolOptions = (ingestionToolsData ?? []).map((t) => ({
    value: t.name,
    label: t.display_name,
  }));

  const objectMap = new Map<string, IndustrialObject>(
    (objectData?.items ?? []).map((o) => [o.code, o]),
  );
  const objectOptions: { value: string; label: string; object_type: string }[] =
    (objectData?.items ?? []).map((o) => ({
      value: o.code,
      label: `${o.display_name} (${o.code})`,
      object_type: o.object_type,
    }));
  const objectTypeOptions = (objectTypeData ?? []).map((t) => ({
    value: t.code,
    label: t.display_name,
  }));

  const { data: deptData } = useQuery({
    queryKey: ['departments-for-component-filter'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptOptions = (deptData?.items ?? []).map((d) => ({
    value: d.id,
    label: d.display_name,
  }));
  const deptNameMap = new Map<string, string>(
    (deptData?.items ?? []).map((d) => [d.id, d.display_name]),
  );
  const objectCodeToDeptId = new Map<string, string | null>(
    (objectData?.items ?? []).map((o) => [o.code, o.department_id ?? null]),
  );

  useEffect(() => {
    if (prefillObject && modalOpen && objectOptions.length > 0) {
      form.setFieldsValue({ experimental_object_code: prefillObject });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objectOptions, prefillObject, modalOpen]);

  const { data: equipmentData } = useQuery({
    queryKey: ['equipment-for-component'],
    queryFn: () => apiListEquipment({ limit: 100 }),
  });
  const equipmentOptions = (equipmentData?.items ?? []).map((e) => ({
    value: e.id,
    label: e.display_name,
  }));

  const allItems: ComponentSummary[] = (() => {
    const items = data?.items ?? [];
    const latestByName = new Map<string, ComponentSummary>();
    for (const item of items) {
      const existing = latestByName.get(item.name);
      if (!existing || compareVersions(item.version, existing.version) > 0) {
        latestByName.set(item.name, item);
      }
    }
    return Array.from(latestByName.values());
  })();

  const modernItems = allItems.filter((i) => i.status !== 'deprecated');
  const archivedItems = allItems.filter((i) => i.status === 'deprecated');
  let currentItems = activeTab === 'modern' ? modernItems : archivedItems;

  if (deptFilter) {
    currentItems = currentItems.filter((i) => {
      const deptId = i.experimental_object_code ? objectCodeToDeptId.get(i.experimental_object_code) : null;
      return deptId === deptFilter;
    });
  }
  if (equipmentFilter) {
    currentItems = currentItems.filter((i) => i.equipment_id === equipmentFilter);
  }

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['component', detailId],
    queryFn: () => apiGetComponent(detailId!),
    enabled: !!detailId,
  });

  const publishMutation = useMutation({
    mutationFn: apiPublishComponent,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      void queryClient.refetchQueries({ queryKey: ['components'] });
      if (detailId) {
        void queryClient.invalidateQueries({ queryKey: ['component', detailId] });
        void queryClient.refetchQueries({ queryKey: ['component-versions', detailId] });
      }
      setDetailId(data.id);
      setModalOpen(false);
      setEditModalOpen(false);
      setEditOriginalName(undefined);
      form.resetFields();
      editForm.resetFields();
      setAdvancedMode(false);
      setEditAdvancedMode(false);
      message.success('组件发布成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  const archiveMutation = useMutation({
    mutationFn: apiArchiveComponent,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      void queryClient.refetchQueries({ queryKey: ['components'] });
      message.success('组件已归档');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const restoreMutation = useMutation({
    mutationFn: apiRestoreComponent,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      void queryClient.refetchQueries({ queryKey: ['components'] });
      message.success('组件已恢复');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: apiDeleteComponent,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      void queryClient.refetchQueries({ queryKey: ['components'] });
      message.success('组件已删除');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const handleOpenModal = (): void => {
    form.resetFields();
    form.setFieldsValue({ department_id: currentUser?.departmentId ?? undefined });
    setAdvancedMode(false);
    setModalOpen(true);
  };

  const handleNewModeSwitch = (checked: boolean): void => {
    if (checked) {
      const formValues = form.getFieldsValue([...FORM_FIELD_NAMES]);
      const yaml = buildManifestYaml({
        display_name: (formValues.display_name as string) ?? '',
        description: (formValues.description as string) ?? '',
        prompt: (formValues.prompt as string) ?? '',
        experimental_object_code: (formValues.experimental_object_code as string) ?? '',
        tool_type: (formValues.tool_type as string) ?? 'llm_converter',
      });
      form.setFieldsValue({ manifest_yaml: yaml, ...FRESH_FORM_VALUES });
    } else {
      const yaml = (form.getFieldValue('manifest_yaml') as string) ?? '';
      const parsed = parseYamlToFormValues(yaml);
      form.setFieldsValue({
        ...FRESH_FORM_VALUES,
        display_name: parsed.display_name,
        description: parsed.description,
        prompt: parsed.prompt,
        experimental_object_code: parsed.experimental_object_code,
        tool_type: parsed.tool_type ?? 'llm_converter',
        manifest_yaml: undefined,
      });
    }
    setAdvancedMode(checked);
  };

  const handlePublish = async (): Promise<void> => {
    try {
      if (advancedMode) {
        const values = await form.validateFields(['manifest_yaml', 'experimental_object_code', 'department_id']);
        publishMutation.mutate({
          manifest_yaml: values.manifest_yaml as string,
          experimental_object_code: (values.experimental_object_code as string) ?? null,
          department_id: (values.department_id as string) ?? null,
        });
      } else {
        const values = await form.validateFields([...FORM_FIELD_NAMES, 'department_id']);
        const yaml = buildManifestYaml({
          display_name: values.display_name as string,
          description: values.description as string,
          prompt: values.prompt as string,
          experimental_object_code: (values.experimental_object_code as string) ?? '',
          tool_type: (values.tool_type as string) ?? 'llm_converter',
        });
        publishMutation.mutate({
          manifest_yaml: yaml,
          experimental_object_code: (values.experimental_object_code as string) ?? null,
          equipment_id: (values.equipment_id as string) ?? null,
          department_id: (values.department_id as string) ?? null,
        });
      }
    } catch {
      // 表单校验失败
    }
  };

  const handleOpenEdit = async (record?: ComponentSummary): Promise<void> => {
    let compDetail;
    const targetId = record?.id ?? detailId;
    if (!targetId) return;
    try {
      compDetail = await apiGetComponent(targetId);
    } catch {
      return;
    }
    if (!compDetail) return;
    const yaml = compDetail.manifest_yaml;
    const parsed = parseYamlToFormValues(yaml);
    setEditOriginalName(compDetail.name);
    editForm.resetFields();
    editForm.setFieldsValue({
      manifest_yaml: yaml,
      display_name: parsed.display_name,
      description: parsed.description,
      prompt: parsed.prompt,
      experimental_object_code: parsed.experimental_object_code ?? compDetail.experimental_object_code,
      equipment_id: compDetail.equipment_id,
      tool_type: parsed.tool_type ?? 'llm_converter',
      department_id: (compDetail as Record<string, unknown>).department_id as string | undefined ?? currentUser?.departmentId,
    });
    setEditAdvancedMode(false);
    setEditModalOpen(true);
  };

  const handleEditModeSwitch = (checked: boolean): void => {
    if (checked) {
      const formValues = editForm.getFieldsValue([...FORM_FIELD_NAMES]);
      const yaml = buildManifestYaml({
        display_name: (formValues.display_name as string) ?? '',
        description: (formValues.description as string) ?? '',
        prompt: (formValues.prompt as string) ?? '',
        experimental_object_code: (formValues.experimental_object_code as string) ?? '',
        tool_type: (formValues.tool_type as string) ?? 'llm_converter',
      }, editOriginalName);
      editForm.setFieldsValue({ manifest_yaml: yaml, ...FRESH_FORM_VALUES });
    } else {
      const yaml = (editForm.getFieldValue('manifest_yaml') as string) ?? '';
      const parsed = parseYamlToFormValues(yaml);
      editForm.setFieldsValue({
        ...FRESH_FORM_VALUES,
        display_name: parsed.display_name,
        description: parsed.description,
        prompt: parsed.prompt,
        experimental_object_code: parsed.experimental_object_code,
        tool_type: parsed.tool_type ?? 'llm_converter',
        manifest_yaml: undefined,
      });
    }
    setEditAdvancedMode(checked);
  };

  const handleEditPublish = async (): Promise<void> => {
    try {
      if (editAdvancedMode) {
        const values = await editForm.validateFields(['manifest_yaml', 'department_id']);
        publishMutation.mutate({ manifest_yaml: values.manifest_yaml as string, department_id: (values.department_id as string) ?? null });
      } else {
        const values = await editForm.validateFields([...FORM_FIELD_NAMES, 'department_id']);
        const yaml = buildManifestYaml({
          display_name: values.display_name as string,
          description: values.description as string,
          prompt: values.prompt as string,
          experimental_object_code: (values.experimental_object_code as string) ?? '',
          tool_type: (values.tool_type as string) ?? 'llm_converter',
        }, editOriginalName);
        publishMutation.mutate({
          manifest_yaml: yaml,
          experimental_object_code: (values.experimental_object_code as string) ?? null,
          equipment_id: (values.equipment_id as string) ?? null,
          department_id: (values.department_id as string) ?? null,
        });
      }
    } catch {
      // 表单校验失败
    }
  };

  const columns: ColumnsType<ComponentSummary> = [
    {
      title: '名称',
      key: 'name',
      width: 200,
      render: (_: unknown, record: ComponentSummary) => (
        <Tooltip title={record.description || undefined} placement="topLeft">
          <Space size={6}>
            <Text strong>{record.display_name || record.name}</Text>
            {record.display_name && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {record.name}
              </Text>
            )}
          </Space>
        </Tooltip>
      ),
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 100,
    },
    {
      title: '实验对象',
      dataIndex: 'experimental_object_code',
      key: 'experimental_object_code',
      width: 200,
      render: (code: string) => {
        if (!code) return <Text type="secondary">-</Text>;
        const obj = objectMap.get(code);
        if (!obj) return <Text code>{code}</Text>;
        return (
          <Tag color="green" style={{ margin: 0, padding: '2px 10px', borderRadius: 4 }}>
            {obj.display_name}
          </Tag>
        );
      },
    },
    {
      title: '归属单位',
      dataIndex: 'department_id',
      key: 'department_id',
      width: 150,
      render: (deptId: string | null) => {
        if (!deptId) return <Text type="secondary">-</Text>;
        return deptNameMap.get(deptId) ?? <Text code>{deptId.slice(0, 8)}</Text>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] ?? 'default'}>
          {STATUS_LABEL[v] ?? v}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: activeTab === 'archived' ? 160 : 120,
      render: (_: unknown, record: ComponentSummary) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={(e) => {
              e.stopPropagation();
              void handleOpenEdit(record);
            }}
          >
            编辑
          </Button>
          {activeTab === 'archived' ? (
            <>
              <Popconfirm
                title="确定恢复该组件？"
                onConfirm={(e) => {
                  e?.stopPropagation();
                  restoreMutation.mutate(record.id);
                }}
                okText="恢复"
                cancelText="取消"
              >
                <Button
                  type="link"
                  size="small"
                  onClick={(e) => e.stopPropagation()}
                  loading={restoreMutation.isPending}
                >
                  恢复
                </Button>
              </Popconfirm>
              <Popconfirm
                title="确定彻底删除该组件？"
                description="此操作不可撤销，将删除组件及其所有版本"
                onConfirm={(e) => {
                  e?.stopPropagation();
                  deleteMutation.mutate(record.id);
                }}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button
                  type="link"
                  size="small"
                  danger
                  onClick={(e) => e.stopPropagation()}
                  loading={deleteMutation.isPending}
                >
                  删除
                </Button>
              </Popconfirm>
            </>
          ) : (
            <Popconfirm
              title="确定归档该组件？"
              onConfirm={(e) => {
                e?.stopPropagation();
                archiveMutation.mutate(record.id);
              }}
              okText="归档"
              cancelText="取消"
            >
              <Button
                type="link"
                size="small"
                danger
                onClick={(e) => e.stopPropagation()}
                loading={archiveMutation.isPending}
              >
                归档
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      {!hideList && (
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleOpenModal}>
          新建接口
        </Button>
        <Select
          placeholder="按单位筛选"
          style={{ width: 180 }}
          allowClear
          showSearch
          optionFilterProp="label"
          value={deptFilter}
          onChange={(val: string | undefined) => setDeptFilter(val)}
          options={deptOptions}
        />
        <Select
          placeholder="按设备筛选"
          style={{ width: 200 }}
          allowClear
          showSearch
          optionFilterProp="label"
          value={equipmentFilter}
          onChange={(val: string | undefined) => setEquipmentFilter(val)}
          options={equipmentOptions}
        />
        <Button
          type={activeTab === 'modern' ? 'primary' : 'default'}
          onClick={() => setActiveTab('modern')}
        >
          活跃
        </Button>
        <Button
          type={activeTab === 'archived' ? 'primary' : 'default'}
          onClick={() => setActiveTab('archived')}
        >
          归档
        </Button>
      </Space>
      )}

      {!hideList && (
      <Table<ComponentSummary>
        columns={columns}
        dataSource={currentItems}
        rowKey="id"
        loading={isLoading}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        size="middle"
        onRow={(record) => ({
          onClick: () => setDetailId(record.id),
          style: { cursor: 'pointer' },
        })}
      />
      )}

      {/* 新建接口 Modal */}
      <Modal
        title="新建接口"
        open={modalOpen}
        onOk={handlePublish}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
          setAdvancedMode(false);
        }}
        confirmLoading={publishMutation.isPending}
        okText="发布"
        cancelText="取消"
        width={680}
        destroyOnClose
      >
        <div style={{ marginBottom: 16 }}>
          <Space align="center">
            <Text>高级模式</Text>
            <Switch checked={advancedMode} onChange={handleNewModeSwitch} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {advancedMode ? '直接编辑 YAML 全文' : '填写表单字段，自动生成 YAML'}
            </Text>
          </Space>
        </div>
        <Form form={form} layout="vertical">
          <Form.Item name="department_id" label="归属部门">
            <DepartmentSelector placeholder="默认取当前用户部门" allowRoot={true} />
          </Form.Item>
          {advancedMode ? (
            <Form.Item
              name="manifest_yaml"
              label="组件清单 (YAML)"
              rules={[
                { required: true, message: '请粘贴组件清单 YAML' },
                { min: 10, message: '清单内容过短' },
              ]}
            >
              <Input.TextArea
                placeholder={`name: iface_ffffffff  # 自动生成\nkind: ingestion\ndisplay_name: "接口名"\n...`}
                rows={16}
                style={{ fontFamily: 'monospace', fontSize: 13 }}
              />
            </Form.Item>
          ) : (
            <ComponentFormFields objectOptions={objectOptions} objectTypeOptions={objectTypeOptions} equipmentOptions={equipmentOptions} objectMap={objectMap} ingestionToolOptions={ingestionToolOptions} />
          )}
        </Form>
      </Modal>

      {/* 组件详情 Drawer */}
      <Drawer
        title="组件详情"
        open={!!detailId}
        onClose={() => setDetailId(null)}
        width={640}
        loading={detailLoading}
        extra={
          detail && (
            <Button type="primary" size="small" onClick={() => void handleOpenEdit()}>
              编辑
            </Button>
          )
        }
      >
        {detail && <ComponentDetailPanel detail={detail} detailId={detailId!} onVersionChange={setDetailId} />}
      </Drawer>

      {/* 编辑组件 Modal */}
      <Modal
        title="编辑接口"
        open={editModalOpen}
        onOk={handleEditPublish}
        onCancel={() => {
          setEditModalOpen(false);
          setEditOriginalName(undefined);
          editForm.resetFields();
          setEditAdvancedMode(true);
        }}
        confirmLoading={publishMutation.isPending}
        okText="发布新版本"
        cancelText="取消"
        width={680}
        destroyOnClose
      >
        <div style={{ marginBottom: 16 }}>
          <Space align="center">
            <Text>高级模式</Text>
            <Switch checked={editAdvancedMode} onChange={handleEditModeSwitch} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {editAdvancedMode
                ? '直接编辑 YAML 全文'
                : '填写表单字段，自动生成 YAML'}
            </Text>
          </Space>
        </div>
        <Form form={editForm} layout="vertical">
          <Form.Item name="department_id" label="归属部门">
            <DepartmentSelector placeholder="默认取当前用户部门" allowRoot={true} />
          </Form.Item>
          {editAdvancedMode ? (
            <>
              <Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
                修改 YAML 后点击发布，将创建新版本。版本号已自动递增，如需修改请手动调整。
              </Text>
              <Form.Item
                name="manifest_yaml"
                label="组件清单 (YAML)"
                rules={[
                  { required: true, message: '请输入组件清单 YAML' },
                  { min: 10, message: '清单内容过短' },
                ]}
              >
                <Input.TextArea
                  rows={20}
                  style={{ fontFamily: 'monospace', fontSize: 13 }}
                />
              </Form.Item>
            </>
          ) : (
            <>
              <Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
                填写表单字段，自动生成 YAML。已从 YAML 提取可匹配的字段。
              </Text>
              <ComponentFormFields objectOptions={objectOptions} objectTypeOptions={objectTypeOptions} equipmentOptions={equipmentOptions} objectMap={objectMap} originalName={editOriginalName} ingestionToolOptions={ingestionToolOptions} />
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}
