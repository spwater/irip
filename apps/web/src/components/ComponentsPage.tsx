import { useEffect, useState } from 'react';
import {
  Button,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiActivateVersion,
  apiArchiveComponent,
  apiDeleteComponent,
  apiGetComponent,
  apiListComponentVersions,
  apiListComponents,
  apiListDepartments,
  apiListEquipment,
  apiListObjects,
  apiPublishComponent,
  apiRestoreComponent,
  extractApiError,
  type ComponentDetail,
  type ComponentSummary,
  type ComponentVersionItem,
  type IndustrialObject,
} from '@/api/client';

/** 把 UTC 时间字符串转成本地时间显示 */
function fmtTime(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString('zh-CN', { hour12: false });
}

const { Title, Text } = Typography;

/** 组件状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  draft: 'blue',
  published: 'green',
  deprecated: 'default',
};

/** 组件状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  deprecated: '已弃用',
};

/** 比较函数 —— 摩登/古法由后端 engine 字段决定（llm=摩登, code=古法） */

/** 比较 semver 版本号，返回 >0/0/<0 */
function compareVersions(a: string, b: string): number {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const va = pa[i] ?? 0;
    const vb = pb[i] ?? 0;
    if (va !== vb) return va - vb;
  }
  return 0;
}

/** 表单字段名称集合（表单模式共用） */
const FORM_FIELD_NAMES = [
  'display_name',
  'description',
  'prompt',
  'file_engine',
  'experimental_object_code',
] as const;

/** 表单模式的初始（清空）状态：file_engine 默认 pymupdf，其余为空 */
const FRESH_FORM_VALUES: Record<string, string | undefined> = {
  display_name: undefined,
  description: undefined,
  prompt: undefined,
  file_engine: 'pymupdf',
  experimental_object_code: undefined,
};

/** 表单模式提交时的字段值 */
interface ComponentFormValues {
  display_name: string;
  description: string;
  prompt: string;
  file_engine: string;
  experimental_object_code: string;
}

/**
 * 转义 YAML 双引号字符串中的特殊字符。
 *
 * YAML 双引号字符串支持反斜杠转义序列，可安全承载换行、引号、反斜杠等
 * 任意字符，适合 prompt 这类多行文本。转义顺序：先反斜杠，再其余字符。
 */
function yamlEscapeDouble(value: string): string {
  return value
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t');
}

/**
 * 把表单字段值组装成 ingestion 组件的 manifest YAML。
 *
 * 固定结构：kind 固定 ingestion，inputs 固定 []，outputs 固定 observation_table。
 * name 自动生成，YAML 里显示占位值 iface_ffffffff。
 * version 由系统自动管理，不在 YAML 里。
 */
function buildManifestYaml(v: ComponentFormValues, originalName?: string): string {
  const displayName = v.display_name ?? '';
  const description = v.description ?? '';
  const prompt = v.prompt ?? '';
  const fileEngine = v.file_engine ?? 'pymupdf';
  const expCode = v.experimental_object_code ?? '';
  const nameLine = originalName
    ? `name: ${originalName}`
    : 'name: iface_ffffffff  # 自动生成，无需修改';
  const lines: string[] = [
    nameLine,
    'kind: ingestion',
    `display_name: "${yamlEscapeDouble(displayName)}"`,
    `description: "${yamlEscapeDouble(description)}"`,
    'inputs: []',
    'outputs:',
    '  - name: observations',
    '    data_type: observation_table',
    'parameters:',
    '  type: object',
    '  required: []',
    '  properties:',
    '    path:',
    '      type: string',
    '      description: "文件路径，执行时上传"',
    '    prompt:',
    '      type: string',
    '      description: "LLM 提示词"',
    `      default: |\n        ${prompt.replace(/\n/g, '\n        ')}`,
    '    file_engine:',
    '      type: string',
    '      description: "文件读取方式"',
    `      default: "${yamlEscapeDouble(fileEngine)}"`,
    '    experimental_object_code:',
    '      type: string',
    '      description: "关联实验对象编码"',
    `      default: "${yamlEscapeDouble(expCode)}"`,
    'timeout_seconds: 300',
  ];
  return lines.join('\n');
}

/** 实验对象下拉选项类型 */
type ObjectOption = { value: string; label: string };

/**
 * 从 YAML 文本中尽量提取表单字段值（容错优先）。
 *
 * 每个字段独立正则匹配，提取失败则留 undefined。不抛异常、不报错。
 * 用于高级模式 → 表单模式切换时尽量保留用户已编辑的内容。
 */
function parseYamlToFormValues(yaml: string): Partial<ComponentFormValues> {
  const result: Partial<ComponentFormValues> = {};

  // display_name: "xxx" 或 display_name: xxx
  const dnMatch = yaml.match(/^display_name:[ \t]*["']?(.*?)["']?[ \t]*$/m);
  if (dnMatch) result.display_name = dnMatch[1];

  // description: "xxx" 或 description: xxx
  const descMatch = yaml.match(/^description:[ \t]*["']?(.*?)["']?[ \t]*$/m);
  if (descMatch) result.description = descMatch[1];

  // prompt 的 default 值（支持双引号格式和块标量 | 格式）
  const promptBlockMatch = yaml.match(/prompt:\s*\n\s*type:\s*string\s*\n\s*description:.*?\n\s*default:\s*\|\s*\n((?:\s{8,}.*\n?)*)/m);
  if (promptBlockMatch) {
    // 块标量格式：去掉每行前面的 8 个空格缩进
    result.prompt = promptBlockMatch[1].replace(/^        /gm, '').replace(/\n$/, '');
  } else {
    const promptMatch = yaml.match(/prompt:\s*\n\s*type:\s*string\s*\n\s*description:.*?\n\s*default:\s*["']?(.*?)["']?\s*$/m);
    if (promptMatch) result.prompt = promptMatch[1];
  }

  // file_engine 的 default 值
  const feMatch = yaml.match(/file_engine:\s*\n\s*type:\s*string\s*\n\s*description:.*?\n\s*default:\s*["']?(.*?)["']?\s*$/m);
  if (feMatch) result.file_engine = feMatch[1];

  // experimental_object_code 的 default 值
  const eocMatch = yaml.match(/experimental_object_code:\s*\n\s*type:\s*string\s*\n\s*description:.*?\n\s*default:\s*["']?(.*?)["']?\s*$/m);
  if (eocMatch) result.experimental_object_code = eocMatch[1];

  return result;
}

/** 组件表单字段（表单模式共用，绑定到外层 Form 上下文） */
function ComponentFormFields({
  objectOptions,
  objectMap: _objectMap,
  originalName,
}: {
  objectOptions: ObjectOption[];
  equipmentOptions: ObjectOption[];
  objectMap: Map<string, IndustrialObject>;
  originalName?: string;
}): JSX.Element {
  return (
    <>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="接口编码">
            <Input value={originalName ?? 'iface_ffffffff'} disabled />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item
            name="display_name"
            label="组件名称"
            rules={[{ required: true, message: '请输入组件名称' }]}
          >
            <Input placeholder="例如：XRF-EZ扫描提取器" />
          </Form.Item>
        </Col>
      </Row>
      <Form.Item
        name="description"
        label="描述"
        rules={[{ required: true, message: '请输入描述' }]}
      >
        <Input placeholder="LLM 驱动的文档提取组件" />
      </Form.Item>
      <Form.Item
        name="prompt"
        label="LLM 提示词"
        rules={[{ required: true, message: '请输入 LLM 提示词' }]}
      >
        <Input.TextArea rows={6} placeholder="请输入 LLM 提示词，支持多行" />
      </Form.Item>
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'inline-block', marginRight: 12, lineHeight: '32px', fontWeight: 500 }}>
          文件读取方式
        </div>
        <Form.Item
          name="file_engine"
          initialValue="pymupdf"
          rules={[{ required: true, message: '请选择文件读取方式' }]}
          style={{ display: 'inline-block', marginBottom: 0 }}
        >
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            options={[
              { value: 'pymupdf', label: 'pymupdf' },
              { value: 'image', label: 'image' },
              { value: 'raw', label: 'raw' },
            ]}
          />
        </Form.Item>
      </div>
      <Form.Item name="experimental_object_code" label="关联实验对象">
        <Select
          placeholder="请选择实验对象"
          allowClear
          showSearch
          optionFilterProp="label"
          options={objectOptions}
        />
      </Form.Item>
    </>
  );
}

/**
 * 工具箱页面
 *
 * 分两栏展示：
 * - 摩登：基于 LLM 的组件（如 llm_extractor）
 * - 古法：基于代码的经典组件（csv_reader 等）
 */
export function ComponentsPage({ prefillObject }: { prefillObject?: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<'modern' | 'archived'>('modern');
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editOriginalName, setEditOriginalName] = useState<string | undefined>(undefined);
  // 新建接口：默认表单模式（高级模式关闭）
  const [advancedMode, setAdvancedMode] = useState(false);
  // 编辑组件：默认高级模式（已有完整 YAML）
  const [editAdvancedMode, setEditAdvancedMode] = useState(true);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [detailId, setDetailId] = useState<string | null>(null);

  // ---- 预填：从实验对象页面跳转过来时，自动打开新建弹窗并预填关联实验对象 ----
  useEffect(() => {
    if (prefillObject) {
      form.resetFields();
      setAdvancedMode(false);
      setModalOpen(true);
      form.setFieldsValue({ experimental_object_code: prefillObject, file_engine: 'pymupdf' });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillObject]);

  // ---- 列表查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['components'],
    queryFn: () => apiListComponents(),
  });

  // ---- 实验对象列表查询（用于显示"实验对象"列 + 单位筛选）----
  const { data: objectData } = useQuery({
    queryKey: ['objects-for-component'],
    queryFn: () => apiListObjects({ page_size: 100 }),
  });
  // code → 实验对象（含 equipment_id，用于间接查设备名称）
  const objectMap = new Map<string, IndustrialObject>(
    (objectData?.items ?? []).map((o) => [o.code, o]),
  );
  // 实验对象下拉选项（显示 display_name，值为 code，用于表单模式的选择器）
  const objectOptions: ObjectOption[] = (objectData?.items ?? []).map((o) => ({
    value: o.code,
    label: o.display_name,
  }));

  // ---- 实验室列表查询（用于单位筛选）----
  const { data: deptData } = useQuery({
    queryKey: ['departments-for-component-filter'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptMap = new Map<string, string>(
    (deptData?.items ?? []).map((d) => [d.id, d.display_name]),
  );
  const deptOptions = (deptData?.items ?? []).map((d) => ({
    value: d.id,
    label: d.display_name,
  }));
  // experimental_object_code → department_id 映射（通过 objectMap）
  const objectCodeToDeptId = new Map<string, string | null>(
    (objectData?.items ?? []).map((o) => [o.code, o.department_id ?? null]),
  );

  // 当 objectOptions 异步加载完成后，如果弹窗已打开且有预填值，
  // 重新设置一次 experimental_object_code，确保 Select 在 options 就绪后正确显示 label。
  useEffect(() => {
    if (prefillObject && modalOpen && objectOptions.length > 0) {
      form.setFieldsValue({ experimental_object_code: prefillObject });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objectOptions, prefillObject, modalOpen]);

  // ---- 设备列表查询（用于通过实验对象的 equipment_id 显示关联设备名）----
  const { data: equipmentData } = useQuery({
    queryKey: ['equipment-for-component'],
    queryFn: () => apiListEquipment({ limit: 100 }),
  });
  // id → display_name
  const equipmentMap = new Map(
    (equipmentData?.items ?? []).map((e) => [e.id, e.display_name]),
  );

  // 设备选项（用于表单内设备筛选）
  const equipmentOptions = (equipmentData?.items ?? []).map((e) => ({
    value: e.id,
    label: e.display_name,
  }));

  const allItems: ComponentSummary[] = (() => {
    const items = data?.items ?? [];
    // 按 name 去重，只保留最新版本
    const latestByName = new Map<string, ComponentSummary>();
    for (const item of items) {
      const existing = latestByName.get(item.name);
      if (!existing || compareVersions(item.version, existing.version) > 0) {
        latestByName.set(item.name, item);
      }
    }
    return Array.from(latestByName.values());
  })();

  // 按摩登/归档分组（engine=llm → 摩登，其余归入归档）
  const modernItems = allItems.filter((i) => i.engine === 'llm' && i.status !== 'deprecated');
  const archivedItems = allItems.filter((i) => i.status === 'deprecated');
  let currentItems = activeTab === 'modern' ? modernItems : archivedItems;

  // 按单位筛选（通过 experimental_object_code → department_id 关联）
  if (deptFilter) {
    currentItems = currentItems.filter((i) => {
      const deptId = i.experimental_object_code ? objectCodeToDeptId.get(i.experimental_object_code) : null;
      return deptId === deptFilter;
    });
  }

  // ---- 详情查询 ----
  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['component', detailId],
    queryFn: () => apiGetComponent(detailId!),
    enabled: !!detailId,
  });

  // ---- 发布组件 Mutation（注册 + 编辑共用）----
  const publishMutation = useMutation({
    mutationFn: apiPublishComponent,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      void queryClient.refetchQueries({ queryKey: ['components'] });
      // 刷新旧版本详情和版本历史
      if (detailId) {
        void queryClient.invalidateQueries({ queryKey: ['component', detailId] });
        void queryClient.refetchQueries({ queryKey: ['component-versions', detailId] });
      }
      // 指向新版本，让详情自动刷新
      setDetailId(data.id);
      setModalOpen(false);
      setEditModalOpen(false);
      setEditOriginalName(undefined);
      form.resetFields();
      editForm.resetFields();
      // 重置模式：新建和编辑都回到表单模式
      setAdvancedMode(false);
      setEditAdvancedMode(false);
      message.success('组件发布成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 归档 / 恢复 / 删除 Mutation ----
  const archiveMutation = useMutation({
    mutationFn: apiArchiveComponent,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      message.success('组件已归档');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const restoreMutation = useMutation({
    mutationFn: apiRestoreComponent,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      message.success('组件已恢复');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: apiDeleteComponent,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      message.success('组件已删除');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 事件处理 ----
  const handleOpenModal = (): void => {
    form.resetFields();
    setAdvancedMode(false);
    setModalOpen(true);
  };

  /**
   * 新建接口模式切换：尽量保留内容，能匹配就匹配，不能匹配就留空。
   * - 表单 → 高级：用 buildManifestYaml 把表单值生成 YAML 填入高级模式
   * - 高级 → 表单：用 parseYamlToFormValues 从 YAML 提取字段填入表单
   */
  const handleNewModeSwitch = (checked: boolean): void => {
    if (checked) {
      // 表单 → 高级：从表单值生成 YAML
      const formValues = form.getFieldsValue([...FORM_FIELD_NAMES]);
      const yaml = buildManifestYaml({
        display_name: (formValues.display_name as string) ?? '',
        description: (formValues.description as string) ?? '',
        prompt: (formValues.prompt as string) ?? '',
        file_engine: (formValues.file_engine as string) ?? 'pymupdf',
        experimental_object_code: (formValues.experimental_object_code as string) ?? '',
      });
      form.setFieldsValue({ manifest_yaml: yaml, ...FRESH_FORM_VALUES });
    } else {
      // 高级 → 表单：从 YAML 提取字段
      const yaml = (form.getFieldValue('manifest_yaml') as string) ?? '';
      const parsed = parseYamlToFormValues(yaml);
      form.setFieldsValue({
        ...FRESH_FORM_VALUES,
        display_name: parsed.display_name,
        description: parsed.description,
        prompt: parsed.prompt,
        file_engine: parsed.file_engine ?? 'pymupdf',
        experimental_object_code: parsed.experimental_object_code,
        manifest_yaml: undefined,
      });
    }
    setAdvancedMode(checked);
  };

  const handlePublish = async (): Promise<void> => {
    try {
      if (advancedMode) {
        const values = await form.validateFields(['manifest_yaml', 'experimental_object_code']);
        publishMutation.mutate({
          manifest_yaml: values.manifest_yaml as string,
          experimental_object_code: (values.experimental_object_code as string) ?? null,
        });
      } else {
        const values = await form.validateFields([...FORM_FIELD_NAMES]);
        const yaml = buildManifestYaml({
          display_name: values.display_name as string,
          description: values.description as string,
          prompt: values.prompt as string,
          file_engine: values.file_engine as string,
          experimental_object_code: (values.experimental_object_code as string) ?? '',
        });
        publishMutation.mutate({
          manifest_yaml: yaml,
          experimental_object_code: (values.experimental_object_code as string) ?? null,
        });
      }
    } catch {
      // 表单校验失败
    }
  };

  const handleOpenEdit = async (record?: ComponentSummary): Promise<void> => {
    // 如果传了 record，直接用它；否则用已加载的 detail
    let compDetail = detail;
    if (!compDetail && record) {
      try {
        compDetail = await apiGetComponent(record.id);
      } catch {
        return;
      }
    }
    if (!compDetail) return;
    // 版本号由后端自动管理，前端不需要处理
    const yaml = compDetail.manifest_yaml;
    // 从 YAML 解析表单字段值
    const parsed = parseYamlToFormValues(yaml);
    // 保存原始 name（编辑时 buildManifestYaml 用它而不是占位值）
    setEditOriginalName(compDetail.name);
    editForm.resetFields();
    editForm.setFieldsValue({
      manifest_yaml: yaml,
      display_name: parsed.display_name,
      description: parsed.description,
      prompt: parsed.prompt,
      file_engine: parsed.file_engine,
      experimental_object_code: parsed.experimental_object_code ?? compDetail.experimental_object_code,
    });
    // 编辑默认表单模式
    setEditAdvancedMode(false);
    setEditModalOpen(true);
  };

  /**
   * 编辑组件模式切换：与新建接口一致，尽量保留内容。
   * - 表单 → 高级：用 buildManifestYaml 生成 YAML
   * - 高级 → 表单：用 parseYamlToFormValues 提取字段
   */
  const handleEditModeSwitch = (checked: boolean): void => {
    if (checked) {
      // 表单 → 高级
      const formValues = editForm.getFieldsValue([...FORM_FIELD_NAMES]);
      const yaml = buildManifestYaml({
        display_name: (formValues.display_name as string) ?? '',
        description: (formValues.description as string) ?? '',
        prompt: (formValues.prompt as string) ?? '',
        file_engine: (formValues.file_engine as string) ?? 'pymupdf',
        experimental_object_code: (formValues.experimental_object_code as string) ?? '',
      }, editOriginalName);
      editForm.setFieldsValue({ manifest_yaml: yaml, ...FRESH_FORM_VALUES });
    } else {
      // 高级 → 表单
      const yaml = (editForm.getFieldValue('manifest_yaml') as string) ?? '';
      const parsed = parseYamlToFormValues(yaml);
      editForm.setFieldsValue({
        ...FRESH_FORM_VALUES,
        display_name: parsed.display_name,
        description: parsed.description,
        prompt: parsed.prompt,
        file_engine: parsed.file_engine ?? 'pymupdf',
        experimental_object_code: parsed.experimental_object_code,
        manifest_yaml: undefined,
      });
    }
    setEditAdvancedMode(checked);
  };

  const handleEditPublish = async (): Promise<void> => {
    try {
      if (editAdvancedMode) {
        const values = await editForm.validateFields(['manifest_yaml']);
        publishMutation.mutate({ manifest_yaml: values.manifest_yaml as string });
      } else {
        const values = await editForm.validateFields([...FORM_FIELD_NAMES]);
        let yaml = buildManifestYaml({
          display_name: values.display_name as string,
          description: values.description as string,
          prompt: values.prompt as string,
          file_engine: values.file_engine as string,
          experimental_object_code: (values.experimental_object_code as string) ?? '',
        }, editOriginalName);
        publishMutation.mutate({ manifest_yaml: yaml });
      }
    } catch {
      // 表单校验失败
    }
  };

  // ---- 表格列定义 ----
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
        const eqName = obj.equipment_id
          ? equipmentMap.get(obj.equipment_id)
          : null;
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Tag color="green" style={{ margin: 0, padding: '2px 10px', borderRadius: 4 }}>
              {obj.display_name}
            </Tag>
            {eqName && (
              <>
                <span style={{ color: '#999', fontSize: 14, lineHeight: 1 }}>&#10142;</span>
                <Tag color="cyan" style={{ margin: 0, padding: '2px 10px', borderRadius: 4 }}>
                  {eqName}
                </Tag>
              </>
            )}
          </div>
        );
      },
    },
    {
      title: '所属单位',
      key: 'department',
      width: 140,
      render: (_: unknown, record: ComponentSummary) => {
        const deptId = record.experimental_object_code
          ? objectCodeToDeptId.get(record.experimental_object_code)
          : null;
        const name = deptId ? deptMap.get(deptId) : null;
        return name ? <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{name}</Tag> : <Text type="secondary">-</Text>;
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
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={handleOpenModal}>
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

      {/* 新建接口 Modal（双模式：表单填空 / 高级 YAML 编辑）*/}
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
                placeholder={`name: iface_ffffffff  # 自动生成\nkind: ingestion\ndisplay_name: \"接口名\"\n...`}
                rows={16}
                style={{ fontFamily: 'monospace', fontSize: 13 }}
              />
            </Form.Item>
          ) : (
            <ComponentFormFields objectOptions={objectOptions} equipmentOptions={equipmentOptions} objectMap={objectMap} />
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
        {detail && <ComponentDetailPanel detail={detail} detailId={detailId!} />}
      </Drawer>

      {/* 编辑组件 Modal（双模式：默认高级模式，可切换到表单模式）*/}
      <Modal
        title="编辑组件"
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
              <ComponentFormFields objectOptions={objectOptions} equipmentOptions={equipmentOptions} objectMap={objectMap} originalName={editOriginalName} />
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}

/** 组件详情面板 */
function ComponentDetailPanel({
  detail,
  detailId,
}: {
  detail: ComponentDetail;
  detailId: string;
}): JSX.Element {
  const queryClient = useQueryClient();
  // ---- 版本历史查询 ----
  const { data: versions, isLoading: versionsLoading } = useQuery({
    queryKey: ['component-versions', detailId],
    queryFn: () => apiListComponentVersions(detailId),
  });

  // ---- 回滚 Mutation（切换当前活跃版本）----
  const rollbackMutation = useMutation({
    mutationFn: async (versionId: string) => {
      await apiActivateVersion(versionId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      void queryClient.invalidateQueries({ queryKey: ['component-versions', detailId] });
      void queryClient.refetchQueries({ queryKey: ['component-versions', detailId] });
      message.success('已回滚到该版本');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  return (
    <div>
      <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="名称">
          <Text strong>{detail.display_name || detail.name}</Text>
          {detail.display_name && (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              {detail.name}
            </Text>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="版本">{detail.version}</Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={STATUS_COLOR[detail.status] ?? 'default'}>
            {STATUS_LABEL[detail.status] ?? detail.status}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="发布时间">
          {fmtTime(detail.published_at)}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {fmtTime(detail.created_at)}
        </Descriptions.Item>
      </Descriptions>

      <Title level={5} style={{ marginTop: 24 }}>
        Manifest (YAML)
      </Title>
      <pre
        style={{
          background: '#f5f5f5',
          padding: 16,
          borderRadius: 6,
          fontSize: 13,
          fontFamily: 'monospace',
          overflow: 'auto',
          maxHeight: 320,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {detail.manifest_yaml}
      </pre>

      {/* 版本历史 */}
      <Title level={5} style={{ marginTop: 24 }}>
        版本历史
      </Title>
      {versionsLoading ? (
        <div style={{ textAlign: 'center', padding: 16 }}>
          <Spin size="small" />
        </div>
      ) : versions && versions.length > 0 ? (
        <div style={{ maxHeight: 300, overflow: 'auto' }}>
          {versions.map((v: ComponentVersionItem, idx: number) => {
            const isCurrent = idx === 0;
            return (
              <div
                key={v.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '8px 12px',
                  borderBottom: '1px solid #f0f0f0',
                  background: isCurrent ? '#f6ffed' : 'transparent',
                }}
              >
                <Space size={8}>
                  <Tag color={isCurrent ? 'green' : 'default'}>
                    v{v.version}
                  </Tag>
                  {isCurrent && (
                    <Text type="success" style={{ fontSize: 11 }}>
                      当前
                    </Text>
                  )}
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {fmtTime(v.created_at)}
                  </Text>
                </Space>
                {!isCurrent && (
                  <Popconfirm
                    title={`回滚到 v${v.version}？`}
                    description="将恢复该版本的 manifest 为当前活跃版本"
                    onConfirm={() => rollbackMutation.mutate(v.id)}
                    okText="回滚"
                    cancelText="取消"
                  >
                    <Button
                      type="link"
                      size="small"
                      loading={rollbackMutation.isPending}
                    >
                      回滚
                    </Button>
                  </Popconfirm>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }}>
          暂无其他版本
        </Text>
      )}
    </div>
  );
}
