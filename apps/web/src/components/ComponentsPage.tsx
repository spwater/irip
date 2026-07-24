import { useState } from 'react';
import {
  Button,
  Card,
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
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiArchiveComponent,
  apiDeleteComponent,
  apiGetComponent,
  apiListComponentVersions,
  apiListComponents,
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

/** 组件类别 → 中文标签 */
const KIND_LABEL: Record<string, string> = {
  ingestion: '数据接入',
  transform: '数据转换',
  quality: '质量校验',
  statistics: '统计分析',
  output: '结果输出',
  model: '模型推理',
};

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
  'name',
  'display_name',
  'description',
  'prompt',
  'file_engine',
  'experimental_object_code',
] as const;

/** 表单模式的初始（清空）状态：file_engine 默认 pymupdf，其余为空 */
const FRESH_FORM_VALUES: Record<string, string | undefined> = {
  name: undefined,
  display_name: undefined,
  description: undefined,
  prompt: undefined,
  file_engine: 'pymupdf',
  experimental_object_code: undefined,
};

/** 表单模式提交时的字段值 */
interface ComponentFormValues {
  name: string;
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
 * 固定结构：version 固定 "1.0.0"（后端自动递增），kind 固定 ingestion，
 * inputs 固定 []，outputs 固定 observation_table。
 */
function buildManifestYaml(v: ComponentFormValues): string {
  const name = v.name ?? '';
  const displayName = v.display_name ?? '';
  const description = v.description ?? '';
  const prompt = v.prompt ?? '';
  const fileEngine = v.file_engine ?? 'pymupdf';
  const expCode = v.experimental_object_code ?? '';
  const lines: string[] = [
    `name: ${name}`,
    'version: "1.0.0"',
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
    `      default: "${yamlEscapeDouble(prompt)}"`,
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

  // name: xxx（顶层，无引号）—— 用 [ \t]* 代替 \s* 避免空值时跨行匹配下一行内容
  const nameMatch = yaml.match(/^name:[ \t]*(\S+)/m);
  if (nameMatch) result.name = nameMatch[1];

  // display_name: "xxx" 或 display_name: xxx
  const dnMatch = yaml.match(/^display_name:[ \t]*["']?(.*?)["']?[ \t]*$/m);
  if (dnMatch) result.display_name = dnMatch[1];

  // description: "xxx" 或 description: xxx
  const descMatch = yaml.match(/^description:[ \t]*["']?(.*?)["']?[ \t]*$/m);
  if (descMatch) result.description = descMatch[1];

  // prompt 的 default 值（parameters → properties → prompt → default）
  const promptMatch = yaml.match(/prompt:\s*\n\s*type:\s*string\s*\n\s*description:.*?\n\s*default:\s*["']?(.*?)["']?\s*$/m);
  if (promptMatch) result.prompt = promptMatch[1];

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
  equipmentOptions,
  objectMap,
}: {
  objectOptions: ObjectOption[];
  equipmentOptions: ObjectOption[];
  objectMap: Map<string, IndustrialObject>;
}): JSX.Element {
  const [eqFilter, setEqFilter] = useState<string | undefined>(undefined);

  // 按选中的设备筛选实验对象选项
  const filteredObjectOptions = eqFilter
    ? objectOptions.filter((opt) => {
        const obj = objectMap.get(opt.value);
        return obj?.equipment_id === eqFilter;
      })
    : objectOptions;

  return (
    <>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item
            name="name"
            label="组件编码"
            rules={[
              { required: true, message: '请输入组件编码' },
              {
                pattern: /^[a-z][a-z0-9_]*$/,
                message: '仅允许小写字母/数字/下划线，且以字母开头',
              },
            ]}
          >
            <Input placeholder="例如：xrf_ez_extractor" />
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
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="关联设备筛选">
            <Select
              placeholder="按设备筛选实验对象"
              allowClear
              showSearch
              optionFilterProp="label"
              options={equipmentOptions}
              onChange={(val: string | undefined) => setEqFilter(val ?? undefined)}
            />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="experimental_object_code" label="实验对象">
            <Select
              placeholder="请选择实验对象"
              allowClear
              showSearch
              optionFilterProp="label"
              options={filteredObjectOptions}
            />
          </Form.Item>
        </Col>
      </Row>
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
export function ComponentsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<'modern' | 'classic' | 'archived'>('modern');
  const [kindFilter, setKindFilter] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  // 新建工具：默认表单模式（高级模式关闭）
  const [advancedMode, setAdvancedMode] = useState(false);
  // 编辑组件：默认高级模式（已有完整 YAML）
  const [editAdvancedMode, setEditAdvancedMode] = useState(true);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [detailId, setDetailId] = useState<string | null>(null);

  // ---- 列表查询 ----
  const { data, isLoading } = useQuery({
    queryKey: ['components', kindFilter],
    queryFn: () => apiListComponents({ kind: kindFilter }),
  });

  // ---- 实验对象列表查询（用于显示"实验对象"列）----
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

  // 按摩登/古法/归档分组（engine=llm → 摩登，engine=code → 古法）
  const modernItems = allItems.filter((i) => i.engine === 'llm' && i.status !== 'deprecated');
  const classicItems = allItems.filter((i) => i.engine !== 'llm' && i.status !== 'deprecated');
  const archivedItems = allItems.filter((i) => i.status === 'deprecated');
  const currentItems = activeTab === 'modern' ? modernItems : activeTab === 'classic' ? classicItems : archivedItems;

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
      // 刷新旧版本详情和版本历史
      if (detailId) {
        void queryClient.invalidateQueries({ queryKey: ['component', detailId] });
        void queryClient.invalidateQueries({ queryKey: ['component-versions', detailId] });
      }
      // 指向新版本，让详情自动刷新
      setDetailId(data.id);
      setModalOpen(false);
      setEditModalOpen(false);
      form.resetFields();
      editForm.resetFields();
      // 重置模式：新建回到表单模式，编辑回到高级模式
      setAdvancedMode(false);
      setEditAdvancedMode(true);
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
   * 新建工具模式切换：尽量保留内容，能匹配就匹配，不能匹配就留空。
   * - 表单 → 高级：用 buildManifestYaml 把表单值生成 YAML 填入高级模式
   * - 高级 → 表单：用 parseYamlToFormValues 从 YAML 提取字段填入表单
   */
  const handleNewModeSwitch = (checked: boolean): void => {
    if (checked) {
      // 表单 → 高级：从表单值生成 YAML
      const formValues = form.getFieldsValue([...FORM_FIELD_NAMES]);
      const yaml = buildManifestYaml({
        name: (formValues.name as string) ?? '',
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
        name: parsed.name,
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
        const values = await form.validateFields(['manifest_yaml']);
        publishMutation.mutate({ manifest_yaml: values.manifest_yaml as string });
      } else {
        const values = await form.validateFields([...FORM_FIELD_NAMES]);
        const yaml = buildManifestYaml({
          name: values.name as string,
          display_name: values.display_name as string,
          description: values.description as string,
          prompt: values.prompt as string,
          file_engine: values.file_engine as string,
          experimental_object_code: (values.experimental_object_code as string) ?? '',
        });
        publishMutation.mutate({ manifest_yaml: yaml });
      }
    } catch {
      // 表单校验失败
    }
  };

  const handleOpenEdit = (): void => {
    if (!detail) return;
    // 自动递增版本号（如 1.0.0 → 1.0.1）
    let yaml = detail.manifest_yaml;
    const versionMatch = yaml.match(/^version:\s*["']?(\d+)\.(\d+)\.(\d+)["']?/m);
    if (versionMatch) {
      const newVersion = `${versionMatch[1]}.${versionMatch[2]}.${Number(versionMatch[3]) + 1}`;
      yaml = yaml.replace(/^version:\s*["']?\d+\.\d+\.\d+["']?/m, `version: "${newVersion}"`);
    }
    editForm.resetFields();
    editForm.setFieldsValue({ manifest_yaml: yaml });
    // 编辑默认高级模式（有完整 YAML）
    setEditAdvancedMode(true);
    setEditModalOpen(true);
  };

  /**
   * 编辑组件模式切换：与新建工具一致，尽量保留内容。
   * - 表单 → 高级：用 buildManifestYaml 生成 YAML
   * - 高级 → 表单：用 parseYamlToFormValues 提取字段
   */
  const handleEditModeSwitch = (checked: boolean): void => {
    if (checked) {
      // 表单 → 高级
      const formValues = editForm.getFieldsValue([...FORM_FIELD_NAMES]);
      const yaml = buildManifestYaml({
        name: (formValues.name as string) ?? '',
        display_name: (formValues.display_name as string) ?? '',
        description: (formValues.description as string) ?? '',
        prompt: (formValues.prompt as string) ?? '',
        file_engine: (formValues.file_engine as string) ?? 'pymupdf',
        experimental_object_code: (formValues.experimental_object_code as string) ?? '',
      });
      editForm.setFieldsValue({ manifest_yaml: yaml, ...FRESH_FORM_VALUES });
    } else {
      // 高级 → 表单
      const yaml = (editForm.getFieldValue('manifest_yaml') as string) ?? '';
      const parsed = parseYamlToFormValues(yaml);
      editForm.setFieldsValue({
        ...FRESH_FORM_VALUES,
        name: parsed.name,
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
        const yaml = buildManifestYaml({
          name: values.name as string,
          display_name: values.display_name as string,
          description: values.description as string,
          prompt: values.prompt as string,
          file_engine: values.file_engine as string,
          experimental_object_code: (values.experimental_object_code as string) ?? '',
        });
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
          <div>
            <Text strong>{record.display_name || record.name}</Text>
            {record.display_name && (
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {record.name}
                </Text>
              </div>
            )}
          </div>
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
      title: '类别',
      dataIndex: 'kind',
      key: 'kind',
      width: 120,
      render: (v: string) => (
        <Tag color={activeTab === 'modern' ? 'purple' : 'blue'}>
          {KIND_LABEL[v] ?? v}
        </Tag>
      ),
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
          <div>
            <Text>{obj.display_name}</Text>
            {eqName && (
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {eqName}
                </Text>
              </div>
            )}
          </div>
        );
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
              setDetailId(record.id);
            }}
          >
            详情
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
          新建工具
        </Button>
        <Select
          placeholder="类别筛选"
          style={{ width: 160 }}
          value={kindFilter ?? '__all__'}
          onChange={(val: string) => setKindFilter(val === '__all__' ? undefined : val)}
          options={[
            { value: '__all__', label: '全部' },
            { value: 'ingestion', label: '数据接入' },
            { value: 'transform', label: '数据转换' },
            { value: 'quality', label: '质量校验' },
            { value: 'statistics', label: '统计分析' },
            { value: 'output', label: '结果输出' },
            { value: 'model', label: '模型推理' },
          ]}
        />
      </Space>

      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key as 'modern' | 'classic' | 'archived')}
          items={[
            {
              key: 'modern',
              label: (
                <span>
                  <Tag color="purple" style={{ marginRight: 4 }}>AI</Tag>
                  摩登
                  <Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>
                    ({modernItems.length})
                  </Text>
                </span>
              ),
              children: (
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
              ),
            },
            {
              key: 'classic',
              label: (
                <span>
                  <Tag color="blue" style={{ marginRight: 4 }}>Code</Tag>
                  古法
                  <Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>
                    ({classicItems.length})
                  </Text>
                </span>
              ),
              children: (
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
              ),
            },
            {
              key: 'archived',
              label: (
                <span>
                  <Tag color="default" style={{ marginRight: 4 }}>Archived</Tag>
                  归档
                  <Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>
                    ({archivedItems.length})
                  </Text>
                </span>
              ),
              children: (
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
              ),
            },
          ]}
        />
      </Card>

      {/* 新建工具 Modal（双模式：表单填空 / 高级 YAML 编辑）*/}
      <Modal
        title="新建工具"
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
                placeholder={`name: my_component\nversion: "1.0.0"\nkind: transform\n...`}
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
            <Button type="primary" size="small" onClick={handleOpenEdit}>
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
              <ComponentFormFields objectOptions={objectOptions} equipmentOptions={equipmentOptions} objectMap={objectMap} />
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
  const [rollbackVersion, setRollbackVersion] = useState<string | null>(null);

  // ---- 版本历史查询 ----
  const { data: versions, isLoading: versionsLoading } = useQuery({
    queryKey: ['component-versions', detailId],
    queryFn: () => apiListComponentVersions(detailId),
  });

  // ---- 回滚 Mutation（用旧版本 manifest 重新发布）----
  const rollbackMutation = useMutation({
    mutationFn: async (versionId: string) => {
      // 获取旧版本详情（拿 manifest_yaml）
      const oldDetail = await apiGetComponent(versionId);
      const oldManifest = oldDetail.manifest_yaml;
      // 自动递增版本号
      let yaml = oldManifest;
      const versionMatch = yaml.match(/^version:\s*["']?(\d+)\.(\d+)\.(\d+)["']?/m);
      if (versionMatch) {
        const newVersion = `${versionMatch[1]}.${versionMatch[2]}.${Number(versionMatch[3]) + 1}`;
        yaml = yaml.replace(/^version:\s*["']?\d+\.\d+\.\d+["']?/m, `version: "${newVersion}"`);
      }
      return apiPublishComponent({ manifest_yaml: yaml });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      void queryClient.invalidateQueries({ queryKey: ['component-versions', detailId] });
      setRollbackVersion(null);
      message.success('已回滚并发布新版本');
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
        <Descriptions.Item label="类别">
          <Tag color="blue">{KIND_LABEL[detail.kind] ?? detail.kind}</Tag>
        </Descriptions.Item>
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
          {versions.map((v: ComponentVersionItem) => {
            const isCurrent = v.id === detailId;
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
                    description="将用该版本的 manifest 发布一个新版本号"
                    onConfirm={() => setRollbackVersion(v.id)}
                    okText="回滚"
                    cancelText="取消"
                  >
                    <Button
                      type="link"
                      size="small"
                      loading={rollbackVersion === v.id && rollbackMutation.isPending}
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
