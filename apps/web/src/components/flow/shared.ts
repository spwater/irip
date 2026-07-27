/**
 * FlowDetail 共享常量、类型、工具函数
 *
 * 从 FlowDetail.tsx 提取，供主组件和子组件共用。
 */

// ---- 状态映射 ----

/** 流程状态 → 颜色 */
export const STATUS_COLOR: Record<string, string> = {
  draft: 'blue',
  published: 'green',
  deprecated: 'default',
};

/** 流程状态 → 中文标签 */
export const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  deprecated: '已弃用',
};

/** 运行状态 → 颜色 */
export const RUN_STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  succeeded: 'green',
  failed: 'red',
  cancelled: 'orange',
  paused: 'gold',
};

/** 运行状态 → 中文标签 */
export const RUN_STATUS_LABEL: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  paused: '已暂停',
};

// ---- 工具函数 ----

/** 把 UTC 时间字符串转成本地时间显示 */
export function fmtTime(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

/** 比较语义化版本号，返回 >0/0/<0 */
export function compareSemver(a: string, b: string): number {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const va = pa[i] ?? 0;
    const vb = pb[i] ?? 0;
    if (va !== vb) return va - vb;
  }
  return 0;
}

/** 生成唯一 key */
let _keyCounter = 0;
export function genKey(): string {
  _keyCounter += 1;
  return `vk_${Date.now()}_${_keyCounter}`;
}

/** 将 YAML 中的标量值字符串转换为对应的 JS 类型 */
export function parseScalarValue(value: string): unknown {
  const v = value.trim();
  if (v === '' || v === '""' || v === "''") return '';
  if (v === 'true') return true;
  if (v === 'false') return false;
  if (v === 'null' || v === '~') return null;
  const num = Number(v);
  if (!isNaN(num) && v !== '') return num;
  return v.replace(/^["']|["']$/g, '');
}

/** 判断参数类型是否为复杂类型（需要 JSON 输入） */
export function isComplexType(type: string): boolean {
  const t = type.toLowerCase();
  return t === 'array' || t === 'object' || t === 'dict' || t === 'list' || t === 'map';
}

/** 将表单输入字符串转换为参数类型对应的值 */
export function convertParamValue(value: string, type: string): unknown {
  const t = type.toLowerCase();
  if (t === 'int' || t === 'integer' || t === 'long') {
    return value === '' ? '' : Math.round(Number(value));
  }
  if (t === 'float' || t === 'double' || t === 'number') {
    return value === '' ? '' : Number(value);
  }
  if (t === 'bool' || t === 'boolean') {
    return value === 'true' || value === '1';
  }
  return value;
}

// ---- 类型定义 ----

/** 解析出的参数定义 */
export type ParamDef = {
  name: string;
  type: string;
  required: boolean;
  default: unknown;
  description: string;
};

/** 解析出的端口定义 */
export type PortDef = {
  name: string;
  type: string;
};

/** 解析出的 manifest 结构 */
export type ParsedManifest = {
  params: ParamDef[];
  inputs: PortDef[];
  outputs: PortDef[];
};

/** 可视化节点构建器中的节点项 */
export type VisualNode = {
  key: string;
  node_id: string;
  component_name: string;
  component_version: string;
  component_id: string;
  params: Record<string, unknown>;
  paramsJson: string;
  useJsonParams: boolean;
};

/** 可视化边构建器中的边项 */
export type VisualEdge = {
  key: string;
  source_node: string;
  source_port: string;
  target_node: string;
  target_port: string;
};

// ---- parseManifest ----

/**
 * 从 manifest_yaml 中解析参数定义和端口定义。
 * 采用简单的行解析，不依赖 js-yaml 库。
 */
export function parseManifest(manifestYaml: string): ParsedManifest {
  const result: ParsedManifest = { params: [], inputs: [], outputs: [] };
  if (!manifestYaml || typeof manifestYaml !== 'string') return result;

  const lines = manifestYaml.split('\n');

  /** 找到顶层 key 所在的行号 */
  const findTopKey = (key: string): number => {
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();
      if (trimmed === `${key}:` || trimmed.startsWith(`${key}:`)) {
        if (!line.startsWith(' ') && !line.startsWith('\t')) {
          return i;
        }
      }
    }
    return -1;
  };

  /** 找到 start 行之后下一个顶层 key 的行号 */
  const findNextTopKey = (start: number): number => {
    for (let i = start + 1; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();
      if (trimmed && !trimmed.startsWith('#') && !line.startsWith(' ') && !line.startsWith('\t')) {
        return i;
      }
    }
    return lines.length;
  };

  // ---- 解析 inputs / outputs ----
  const parsePorts = (sectionName: 'inputs' | 'outputs'): PortDef[] => {
    const ports: PortDef[] = [];
    const start = findTopKey(sectionName);
    if (start < 0) return ports;

    // 检查是否为内联空列表（如 inputs: []）
    const lineContent = lines[start].trim();
    if (/\[\s*\]/.test(lineContent)) return ports;

    const end = findNextTopKey(start);
    const sectionLines = lines.slice(start + 1, end);
    let currentPort: PortDef | null = null;

    for (const line of sectionLines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;

      if (trimmed.startsWith('- ')) {
        if (currentPort) ports.push(currentPort);
        const content = trimmed.slice(2).trim();
        const nameMatch = content.match(/^name:\s*(.*)$/);
        currentPort = {
          name: nameMatch
            ? nameMatch[1].trim().replace(/^["']|["']$/g, '')
            : content.replace(/^["']|["']$/g, ''),
          type: '',
        };
      } else if (currentPort) {
        const match = trimmed.match(/^([\w_]+)\s*:\s*(.*)$/);
        if (match) {
          const [, key, val] = match;
          if (key === 'data_type' || key === 'type') {
            currentPort.type = val.trim().replace(/^["']|["']$/g, '');
          }
        }
      }
    }
    if (currentPort) ports.push(currentPort);
    return ports;
  };

  result.inputs = parsePorts('inputs');
  result.outputs = parsePorts('outputs');

  // ---- 解析 parameters（JSON Schema 格式）----
  const paramsStart = findTopKey('parameters');
  if (paramsStart >= 0) {
    const paramsEnd = findNextTopKey(paramsStart);
    const sectionLines = lines.slice(paramsStart + 1, paramsEnd);

    let baseIndent = -1;
    const requiredParams = new Set<string>();
    let propertiesStart = -1;

    // 第一遍：找到 required 列表和 properties 位置
    for (let i = 0; i < sectionLines.length; i++) {
      const line = sectionLines[i];
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const indent = line.length - line.trimStart().length;
      if (baseIndent < 0) baseIndent = indent;

      if (indent === baseIndent) {
        if (trimmed.startsWith('required:')) {
          // 先尝试解析同行内联列表格式：required: [a, b, c]
          const inlineMatch = trimmed.match(/required:\s*\[(.*)\]/);
          if (inlineMatch) {
            inlineMatch[1].split(',').forEach((item) => {
              const cleanItem = item.trim().replace(/^["']|["']$/g, '');
              if (cleanItem) requiredParams.add(cleanItem);
            });
          } else {
            // 块列表格式
            for (let j = i + 1; j < sectionLines.length; j++) {
              const reqLine = sectionLines[j];
              const reqTrimmed = reqLine.trim();
              if (!reqTrimmed || reqTrimmed.startsWith('#')) continue;
              const reqIndent = reqLine.length - reqLine.trimStart().length;
              if (reqIndent <= baseIndent) break;
              const reqMatch = reqTrimmed.match(/^-\s*(.*)$/);
              if (reqMatch) {
                requiredParams.add(reqMatch[1].trim().replace(/^["']|["']$/g, ''));
              }
            }
          }
        } else if (trimmed.startsWith('properties:')) {
          propertiesStart = i;
        }
      }
    }

    // 第二遍：解析 properties 下的参数定义
    if (propertiesStart >= 0) {
      let propsIndent = -1;
      let propIndent = -1;
      let currentParam: ParamDef | null = null;

      for (let i = propertiesStart + 1; i < sectionLines.length; i++) {
        const line = sectionLines[i];
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const indent = line.length - line.trimStart().length;

        if (propsIndent < 0) propsIndent = indent;

        if (indent <= propsIndent) {
          // 新的参数 key
          const match = trimmed.match(/^([\w_]+)\s*:\s*(.*)$/);
          if (match) {
            if (currentParam) result.params.push(currentParam);
            currentParam = {
              name: match[1],
              type: 'string',
              required: requiredParams.has(match[1]),
              default: undefined,
              description: '',
            };
            propIndent = -1; // 重置属性缩进
          }
        } else if (currentParam) {
          // 当前参数的属性 — 只处理直接子级
          if (propIndent < 0) propIndent = indent;
          if (indent === propIndent) {
            const match = trimmed.match(/^([\w_]+)\s*:\s*(.*)$/);
            if (match) {
              const [, key, val] = match;
              const cleanVal = val.trim().replace(/^["']|["']$/g, '');
              switch (key) {
                case 'type':
                  currentParam.type = cleanVal;
                  break;
                case 'default':
                  currentParam.default = parseScalarValue(cleanVal);
                  break;
                case 'description':
                  currentParam.description = cleanVal;
                  break;
                default:
                  break;
              }
            }
          }
          // 更深层级的嵌套属性（如 items.properties）被忽略
        }
      }
      if (currentParam) result.params.push(currentParam);
    }
  }

  return result;
}
