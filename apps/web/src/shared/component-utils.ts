/**
 * 组件页面的工具函数和常量。
 *
 * 从 ComponentsPage.tsx 拆出，包含：
 * - 常量：STATUS_COLOR / STATUS_LABEL / FORM_FIELD_NAMES / FRESH_FORM_VALUES
 * - 工具函数：fmtTime / compareVersions / yamlEscapeDouble / buildManifestYaml / parseYamlToFormValues
 * - 类型：ComponentFormValues / ObjectOption
 */

/** 把 UTC 时间字符串转成本地时间显示 */
export function fmtTime(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString('zh-CN', { hour12: false });
}

/** 组件状态 → 颜色 */
export const STATUS_COLOR: Record<string, string> = {
  draft: 'blue',
  published: 'green',
  deprecated: 'default',
};

/** 组件状态 → 中文标签 */
export const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  deprecated: '已弃用',
};

/** 比较 semver 版本号，返回 >0/0/<0 */
export function compareVersions(a: string, b: string): number {
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
export const FORM_FIELD_NAMES = [
  'display_name',
  'description',
  'prompt',
  'experimental_object_code',
  'equipment_id',
  'tool_type',
] as const;

/** 表单模式的初始（清空）状态：其余为空 */
export const FRESH_FORM_VALUES: Record<string, string | undefined> = {
  display_name: undefined,
  description: undefined,
  prompt: undefined,
  experimental_object_code: undefined,
  tool_type: 'llm_converter',
};

/** 表单模式提交时的字段值 */
export interface ComponentFormValues {
  display_name: string;
  description: string;
  prompt: string;
  experimental_object_code: string;
  tool_type: string;
}

/** 实验对象下拉选项类型 */
export type ObjectOption = { value: string; label: string };

/**
 * 转义 YAML 双引号字符串中的特殊字符。
 *
 * YAML 双引号字符串支持反斜杠转义序列，可安全承载换行、引号、反斜杠等
 * 任意字符，适合 prompt 这类多行文本。转义顺序：先反斜杠，再其余字符。
 */
export function yamlEscapeDouble(value: string): string {
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
export function buildManifestYaml(v: ComponentFormValues, originalName?: string): string {
  const displayName = v.display_name ?? '';
  const description = v.description ?? '';
  const prompt = v.prompt ?? '';
  const expCode = v.experimental_object_code ?? '';
  const toolType = v.tool_type ?? 'llm_converter';
  const nameLine = originalName
    ? `name: ${originalName}`
    : 'name: iface_ffffffff  # 自动生成，无需修改';
  const lines: string[] = [
    nameLine,
    'version: 1.0.0',
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
    '      default: "auto"',
    '    experimental_object_code:',
    '      type: string',
    '      description: "关联实验对象编码"',
    `      default: "${yamlEscapeDouble(expCode)}"`,
    '    tool_type:',
    '      type: string',
    '      description: "解析工具类型：llm_converter（大模型）或 xrd_converter（XRD 确定性解析）"',
    `      default: "${yamlEscapeDouble(toolType)}"`,
    'timeout_seconds: 300',
  ];
  return lines.join('\n');
}

/**
 * 从 YAML 文本中尽量提取表单字段值（容错优先）。
 *
 * 每个字段独立正则匹配，提取失败则留 undefined。不抛异常、不报错。
 * 用于高级模式 → 表单模式切换时尽量保留用户已编辑的内容。
 */
export function parseYamlToFormValues(yaml: string): Partial<ComponentFormValues> {
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
    result.prompt = promptBlockMatch[1].replace(/^        /gm, '').replace(/\n$/, '');
  } else {
    const promptMatch = yaml.match(/prompt:\s*\n\s*type:\s*string\s*\n\s*description:.*?\n\s*default:\s*["']?(.*?)["']?\s*$/m);
    if (promptMatch) result.prompt = promptMatch[1];
  }

  // experimental_object_code 的 default 值
  const eocMatch = yaml.match(/experimental_object_code:\s*\n\s*type:\s*string\s*\n\s*description:.*?\n\s*default:\s*["']?(.*?)["']?\s*$/m);
  if (eocMatch) result.experimental_object_code = eocMatch[1];

  // tool_type 的 default 值
  const ttMatch = yaml.match(/tool_type:\s*\n\s*type:\s*string\s*\n\s*description:.*?\n\s*default:\s*["']?(.*?)["']?\s*$/m);
  if (ttMatch) result.tool_type = ttMatch[1];

  return result;
}
