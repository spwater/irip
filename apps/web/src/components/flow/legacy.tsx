/**
 * 已弃用的组件 — 从 FlowDetail.tsx 提取
 *
 * PublishVersionModal、NodeEditorCard、ParamInputRow、FileBrowserModal、EdgeEditorRow
 * 创建时自动发布，不再需要手动发布版本。保留代码以备将来需要复杂流程编排时恢复。
 */

/* eslint-disable @typescript-eslint/no-unused-vars */
import { useState, useEffect, useRef } from 'react';
import {
  Button,
  Divider,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import { useQuery, useQueries } from '@tanstack/react-query';
import {
  apiBrowseFiles,
  apiGetComponent,
  apiListComponents,
  apiUploadFile,
  extractApiError,
  type BrowseResponse,
  type ComponentDetail,
  type ComponentSummary,
  type FlowEdgeSchema,
  type FlowNodeSchema,
} from '@/api/client';
import {
  compareSemver,
  convertParamValue,
  genKey,
  isComplexType,
  parseManifest,
  type ParamDef,
  type ParsedManifest,
  type PortDef,
  type VisualEdge,
  type VisualNode,
} from './shared';

const { Text, Title } = Typography;

// @ts-expect-error — 保留未使用的组件定义
function PublishVersionModal({
  open,
  onClose,
  onPublish,
  publishing,
  existingVersion,
}: {
  open: boolean;
  onClose: () => void;
  onPublish: (body: {
    nodes: FlowNodeSchema[];
    edges: FlowEdgeSchema[];
    random_seed: number;
  }) => void;
  publishing: boolean;
  existingVersion: {
    id: string;
    version: number;
    nodes?: Record<string, unknown>[];
    edges?: Record<string, unknown>[];
    random_seed?: number;
  } | null;
}): JSX.Element {
  const [advancedMode, setAdvancedMode] = useState(false);
  const [nodes, setNodes] = useState<VisualNode[]>([
    { key: 'initial-node', node_id: 'n1', component_name: '', component_version: '', component_id: '', params: {}, paramsJson: '{}', useJsonParams: false },
  ]);
  const [edges, setEdges] = useState<VisualEdge[]>([]);
  const [randomSeed, setRandomSeed] = useState('');
  const [nodesJson, setNodesJson] = useState('');
  const [edgesJson, setEdgesJson] = useState('');

  // 弹窗打开时重置状态
  useEffect(() => {
    if (open) {
      setAdvancedMode(false);
      setNodes([
        { key: genKey(), node_id: 'n1', component_name: '', component_version: '', component_id: '', params: {}, paramsJson: '{}', useJsonParams: false },
      ]);
      setEdges([]);
      setRandomSeed('');
      setNodesJson('');
      setEdgesJson('');
    }
  }, [open]);

  // ---- 组件列表查询 ----
  const { data: componentsData, isLoading: componentsLoading } = useQuery({
    queryKey: ['components-for-publish'],
    queryFn: () => apiListComponents(),
    enabled: open,
  });

  const allComponents: ComponentSummary[] = componentsData?.items ?? [];
  // 按去重只保留最新版本
  const latestByName = new Map<string, ComponentSummary>();
  for (const c of allComponents) {
    const existing = latestByName.get(c.name);
    if (!existing || compareSemver(c.version, existing.version) > 0) {
      latestByName.set(c.name, c);
    }
  }
  const uniqueComponents = Array.from(latestByName.values()).filter((c) => c.status !== 'deprecated');
  const modernComponents = uniqueComponents.filter((c) => c.engine === 'llm');
  const classicComponents = uniqueComponents.filter((c) => c.engine !== 'llm');

  // ---- 组件详情查询（按选中的组件动态获取 manifest）----
  const selectedComponentIds = Array.from(
    new Set(nodes.map((n) => n.component_id).filter(Boolean)),
  );

  const detailQueries = useQueries({
    queries: selectedComponentIds.map((id) => ({
      queryKey: ['component-detail-publish', id] as const,
      queryFn: (): Promise<ComponentDetail> => apiGetComponent(id),
      staleTime: 5 * 60 * 1000,
    })),
  });

  // 构建 componentDetails 映射
  const componentDetails: Record<string, ComponentDetail> = {};
  selectedComponentIds.forEach((id, i) => {
    const query = detailQueries[i];
    if (query?.data) {
      componentDetails[id] = query.data;
    }
  });

  // 构建 parsedManifests 映射
  const parsedManifests: Record<string, ParsedManifest> = {};
  Object.entries(componentDetails).forEach(([id, detail]) => {
    parsedManifests[id] = parseManifest(detail.manifest_yaml);
  });

  // ---- 导入当前设定 ----
  const handleImportCurrent = (): void => {
    if (!existingVersion || !existingVersion.nodes) {
      message.warning('当前流程尚未发布过版本，无可导入的设定');
      return;
    }

    const importedNodes: VisualNode[] = (existingVersion.nodes as FlowNodeSchema[]).map((n) => {
      // 尝试从组件列表中匹配组件 ID
      const comp = allComponents.find((c) => c.name === n.component_name && c.version === n.component_version);
      return {
        key: genKey(),
        node_id: n.node_id,
        component_name: n.component_name,
        component_version: n.component_version,
        component_id: comp?.id ?? '',
        params: (n.params as Record<string, unknown>) ?? {},
        paramsJson: JSON.stringify(n.params ?? {}, null, 2),
        useJsonParams: false,
      };
    });

    const importedEdges: VisualEdge[] = (existingVersion.edges as FlowEdgeSchema[] ?? []).map((e) => ({
      key: genKey(),
      source_node: e.source_node,
      source_port: e.source_port,
      target_node: e.target_node,
      target_port: e.target_port,
    }));

    if (importedNodes.length === 0) {
      message.warning('当前版本无节点定义');
      return;
    }

    setNodes(importedNodes);
    setEdges(importedEdges);
    setRandomSeed(existingVersion.random_seed ? String(existingVersion.random_seed) : '');
    setAdvancedMode(false);
    message.success(`已导入 v${existingVersion.version} 的设定（${importedNodes.length} 个节点，${importedEdges.length} 条边）`);
  };

  // ---- 节点操作 ----
  const addNode = (): void => {
    const existingIds = nodes.map((n) => n.node_id);
    let nextNum = 1;
    while (existingIds.includes(`n${nextNum}`)) {
      nextNum++;
    }
    setNodes([
      ...nodes,
      {
        key: genKey(),
        node_id: `n${nextNum}`,
        component_name: '',
        component_version: '',
        component_id: '',
        params: {},
        paramsJson: '{}',
        useJsonParams: false,
      },
    ]);
  };

  const removeNode = (key: string): void => {
    setNodes(nodes.filter((n) => n.key !== key));
  };

  const updateNode = (key: string, updates: Partial<VisualNode>): void => {
    setNodes(nodes.map((n) => (n.key === key ? { ...n, ...updates } : n)));
  };

  const handleComponentChange = (nodeKey: string, componentId: string): void => {
    const component = allComponents.find((c) => c.id === componentId);
    if (!component) return;
    setNodes(
      nodes.map((n) =>
        n.key === nodeKey
          ? {
              ...n,
              component_id: componentId,
              component_name: component.name,
              component_version: component.version,
              params: {},
              paramsJson: '{}',
              useJsonParams: false,
            }
          : n,
      ),
    );
  };

  // ---- 边操作 ----
  const addEdge = (): void => {
    setEdges([
      ...edges,
      { key: genKey(), source_node: '', source_port: '', target_node: '', target_port: '' },
    ]);
  };

  const removeEdge = (key: string): void => {
    setEdges(edges.filter((e) => e.key !== key));
  };

  const updateEdge = (key: string, updates: Partial<VisualEdge>): void => {
    setEdges(edges.map((e) => (e.key === key ? { ...e, ...updates } : e)));
  };

  // ---- 辅助：获取节点的端口列表 ----
  const nodeIdOptions = nodes.map((n) => ({ value: n.node_id, label: n.node_id }));

  const getNodeOutputPorts = (nodeId: string): PortDef[] => {
    const node = nodes.find((n) => n.node_id === nodeId);
    if (!node || !node.component_id) return [];
    const manifest = parsedManifests[node.component_id];
    if (!manifest) return [];
    return manifest.outputs;
  };

  const getNodeInputPorts = (nodeId: string): PortDef[] => {
    const node = nodes.find((n) => n.node_id === nodeId);
    if (!node || !node.component_id) return [];
    const manifest = parsedManifests[node.component_id];
    if (!manifest) return [];
    return manifest.inputs;
  };

  // ---- 发布处理 ----
  const handlePublish = (): void => {
    if (advancedMode) {
      // 高级模式：解析 JSON
      let parsedNodes: FlowNodeSchema[];
      let parsedEdges: FlowEdgeSchema[];
      try {
        parsedNodes = JSON.parse(nodesJson) as FlowNodeSchema[];
        parsedEdges = edgesJson ? (JSON.parse(edgesJson) as FlowEdgeSchema[]) : [];
      } catch (err) {
        message.error(
          `JSON 解析失败: ${err instanceof Error ? err.message : String(err)}`,
        );
        return;
      }
      onPublish({
        nodes: parsedNodes,
        edges: parsedEdges,
        random_seed: 0,
      });
      return;
    }

    // 可视化模式：验证 & 转换
    if (nodes.length === 0) {
      message.error('请至少添加一个节点');
      return;
    }
    const emptyNodes = nodes.filter((n) => !n.component_name);
    if (emptyNodes.length > 0) {
      message.error('存在未选择组件的节点，请删除或补全');
      return;
    }

    const flowNodes: FlowNodeSchema[] = [];
    for (const n of nodes) {
      let params: Record<string, unknown> = {};

      if (n.useJsonParams) {
        try {
          params = n.paramsJson ? JSON.parse(n.paramsJson) : {};
        } catch (err) {
          message.error(
            `节点 ${n.node_id} 的参数 JSON 解析失败: ${err instanceof Error ? err.message : String(err)}`,
          );
          return;
        }
      } else {
        params = { ...n.params };
        // 从 manifest 填充默认值
        const manifest = n.component_id ? parsedManifests[n.component_id] : undefined;
        if (manifest) {
          for (const param of manifest.params) {
            if (!(param.name in params)) {
              // 留空的参数也存入 params（用空值），这样创建执行时弹窗能显示输入框
              params[param.name] = param.default !== undefined ? param.default : '';
            }
            // 复杂类型的字符串值尝试 JSON 解析
            if (
              isComplexType(param.type) &&
              typeof params[param.name] === 'string'
            ) {
              try {
                params[param.name] = JSON.parse(params[param.name] as string);
              } catch {
                // 保留字符串
              }
            }
          }
        }
      }

      flowNodes.push({
        node_id: n.node_id,
        component_name: n.component_name,
        component_version: n.component_version,
        params,
      });
    }

    const flowEdges: FlowEdgeSchema[] = edges
      .filter((e) => e.source_node && e.target_node)
      .map((e) => ({
        source_node: e.source_node,
        source_port: e.source_port,
        target_node: e.target_node,
        target_port: e.target_port,
      }));

    onPublish({
      nodes: flowNodes,
      edges: flowEdges,
      random_seed: Number(randomSeed) || 0,
    });
  };

  return (
    <Modal
      title="发布流程版本"
      open={open}
      onOk={handlePublish}
      onCancel={onClose}
      confirmLoading={publishing}
      okText="发布"
      cancelText="取消"
      width={780}
    >
      {/* 顶部操作栏：导入当前设定 + 高级模式切换 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Button
          size="small"
          onClick={handleImportCurrent}
          disabled={!existingVersion?.nodes}
        >
          导入当前设定
          {existingVersion?.version && (
            <Tag color="blue" style={{ marginLeft: 6, fontSize: 10 }}>
              v{existingVersion.version}
            </Tag>
          )}
        </Button>
        <Space>
          <Text type="secondary">高级模式（手动 JSON）</Text>
          <Switch checked={advancedMode} onChange={setAdvancedMode} />
        </Space>
      </div>

      {advancedMode ? (
        /* ---- 高级模式：JSON 编辑器 ---- */
        <Form layout="vertical">
          <Form.Item label="节点定义 (JSON)" required>
            <Input.TextArea
              rows={8}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder={`[\n  {"node_id":"n1","component_name":"csv_reader","component_version":"1.0.0","params":{},"input_bindings":{}}\n]`}
              value={nodesJson}
              onChange={(e) => setNodesJson(e.target.value)}
            />
          </Form.Item>
          <Form.Item label="边定义 (JSON，可选)">
            <Input.TextArea
              rows={4}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder={`[\n  {"source_node":"n1","source_port":"out","target_node":"n2","target_port":"in"}\n]`}
              value={edgesJson}
              onChange={(e) => setEdgesJson(e.target.value)}
            />
          </Form.Item>
        </Form>
      ) : (
        /* ---- 可视化模式 ---- */
        <div>
          {/* 节点列表 */}
          <div
            style={{
              marginBottom: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <Title level={5} style={{ margin: 0 }}>
              节点定义
            </Title>
            <Button type="dashed" size="small" onClick={addNode}>
              + 添加节点
            </Button>
          </div>

          {componentsLoading && (
            <div style={{ textAlign: 'center', padding: 16 }}>
              <Spin tip="加载组件列表..." />
            </div>
          )}

          {!componentsLoading &&
            nodes.map((node) => (
              <NodeEditorCard
                key={node.key}
                node={node}
                modernComponents={modernComponents}
                classicComponents={classicComponents}
                componentsLoading={componentsLoading}
                componentDetail={
                  node.component_id ? componentDetails[node.component_id] : undefined
                }
                parsedManifest={
                  node.component_id ? parsedManifests[node.component_id] : undefined
                }
                onComponentChange={(componentId) =>
                  handleComponentChange(node.key, componentId)
                }
                onUpdate={(updates) => updateNode(node.key, updates)}
                onRemove={() => removeNode(node.key)}
              />
            ))}

          {/* 边定义 */}
          <Divider orientation="left" plain>
            边定义（连线）
          </Divider>

          <div
            style={{
              marginBottom: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <Text type="secondary" style={{ fontSize: 12 }}>
              连接节点的输出端口到下游节点的输入端口
            </Text>
            <Button type="dashed" size="small" onClick={addEdge}>
              + 添加边
            </Button>
          </div>

          {edges.length > 0 ? (
            edges.map((edge) => (
              <EdgeEditorRow
                key={edge.key}
                edge={edge}
                nodeIdOptions={nodeIdOptions}
                getOutputPorts={getNodeOutputPorts}
                getInputPorts={getNodeInputPorts}
                onUpdate={(updates) => updateEdge(edge.key, updates)}
                onRemove={() => removeEdge(edge.key)}
              />
            ))
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              暂无连线，单节点流程可不添加
            </Text>
          )}

          <Text type="secondary" style={{ fontSize: 11 }}>
            提示：如需设置 input_bindings，请切换到高级模式
          </Text>
        </div>
      )}
    </Modal>
  );
}

// ============================================================
// 节点编辑卡片
// ============================================================

/**
 * 单个节点的可视化编辑卡片
 *
 * 包含：节点 ID 输入、组件选择器（摩登/古法分组）、参数编辑区
 */
function NodeEditorCard({
  node,
  modernComponents,
  classicComponents,
  componentsLoading,
  componentDetail,
  parsedManifest,
  onComponentChange,
  onUpdate,
  onRemove,
}: {
  node: VisualNode;
  modernComponents: ComponentSummary[];
  classicComponents: ComponentSummary[];
  componentsLoading: boolean;
  componentDetail?: ComponentDetail;
  parsedManifest?: ParsedManifest;
  onComponentChange: (componentId: string) => void;
  onUpdate: (updates: Partial<VisualNode>) => void;
  onRemove: () => void;
}): JSX.Element {
  const hasParsedParams = !!(parsedManifest && parsedManifest.params.length > 0);
  const componentLoading = !!node.component_id && !componentDetail;

  return (
    <div
      style={{
        border: '1px solid #e8e8e8',
        borderRadius: 6,
        padding: 12,
        marginBottom: 12,
        background: '#fafafa',
      }}
    >
      {/* 行 1：节点 ID + 组件选择 + 删除 */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        <Input
          style={{ width: 80 }}
          value={node.node_id}
          onChange={(e) => onUpdate({ node_id: e.target.value })}
          placeholder="n1"
        />
        <Select
          showSearch
          optionFilterProp="label"
          style={{ flex: 1 }}
          placeholder="选择组件"
          value={node.component_id || undefined}
          onChange={(value: string) => onComponentChange(value)}
          loading={componentsLoading}
          allowClear
        >
          {modernComponents.length > 0 && (
            <Select.OptGroup
              key="modern"
              label={
                <span>
                  <Tag color="purple" style={{ marginRight: 4, fontSize: 10 }}>
                    AI
                  </Tag>
                  摩登
                </span>
              }
            >
              {modernComponents.map((c) => (
                <Select.Option
                  key={c.id}
                  value={c.id}
                  label={`${c.name} v${c.version}`}
                >
                  <Space size={4}>
                    <Tag color="purple" style={{ margin: 0, fontSize: 10 }}>
                      AI
                    </Tag>
                    <span>{c.name}</span>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      v{c.version}
                    </Text>
                    {c.kind && (
                      <Tag style={{ fontSize: 10, margin: 0 }}>
                        {c.kind}
                      </Tag>
                    )}
                  </Space>
                </Select.Option>
              ))}
            </Select.OptGroup>
          )}
          {classicComponents.length > 0 && (
            <Select.OptGroup
              key="classic"
              label={
                <span>
                  <Tag color="blue" style={{ marginRight: 4, fontSize: 10 }}>
                    Code
                  </Tag>
                  古法
                </span>
              }
            >
              {classicComponents.map((c) => (
                <Select.Option
                  key={c.id}
                  value={c.id}
                  label={`${c.name} v${c.version}`}
                >
                  <Space size={4}>
                    <Tag color="blue" style={{ margin: 0, fontSize: 10 }}>
                      Code
                    </Tag>
                    <span>{c.name}</span>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      v{c.version}
                    </Text>
                    {c.kind && (
                      <Tag style={{ fontSize: 10, margin: 0 }}>
                        {c.kind}
                      </Tag>
                    )}
                  </Space>
                </Select.Option>
              ))}
            </Select.OptGroup>
          )}
        </Select>
        <Button danger size="small" onClick={onRemove}>
          删除
        </Button>
      </div>

      {/* 行 2：版本显示 */}
      {node.component_name && (
        <div style={{ marginBottom: 8, paddingLeft: 4 }}>
          <Space size={8}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              组件: <Text strong>{node.component_name}</Text>
            </Text>
            <Tag color="green">v{node.component_version}</Tag>
          </Space>
        </div>
      )}

      {/* 行 3：参数编辑区 */}
      {componentLoading ? (
        <div style={{ paddingLeft: 4, padding: '4px 0' }}>
          <Spin size="small" />{' '}
          <Text type="secondary" style={{ fontSize: 12 }}>
            加载组件清单...
          </Text>
        </div>
      ) : node.useJsonParams ? (
        /* JSON 参数编辑模式 */
        <div style={{ paddingLeft: 4 }}>
          <Input.TextArea
            rows={3}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
            value={node.paramsJson}
            onChange={(e) => onUpdate({ paramsJson: e.target.value })}
            placeholder={`{\n  "key": "value"\n}`}
          />
          {hasParsedParams && (
            <Button
              type="link"
              size="small"
              onClick={() => onUpdate({ useJsonParams: false })}
            >
              切换到表单编辑
            </Button>
          )}
        </div>
      ) : hasParsedParams ? (
        /* 表单参数编辑模式 */
        <div style={{ paddingLeft: 4 }}>
          {parsedManifest!.params.map((param) => (
            <ParamInputRow
              key={param.name}
              param={param}
              value={node.params[param.name]}
              onChange={(newValue) =>
                onUpdate({
                  params: { ...node.params, [param.name]: newValue },
                })
              }
            />
          ))}
          <Button
            type="link"
            size="small"
            onClick={() =>
              onUpdate({
                useJsonParams: true,
                paramsJson: JSON.stringify(node.params, null, 2) || '{}',
              })
            }
          >
            切换到 JSON 编辑
          </Button>
        </div>
      ) : node.component_id ? (
        /* 组件已选中但无法解析参数 — JSON fallback */
        <div style={{ paddingLeft: 4 }}>
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
            该组件无参数定义（或无法解析），可手动输入 JSON 参数（可选）
          </Text>
          <Input.TextArea
            rows={3}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
            value={node.paramsJson}
            onChange={(e) => onUpdate({ paramsJson: e.target.value })}
            placeholder={`{\n  "key": "value"\n}`}
          />
        </div>
      ) : null}
    </div>
  );
}

// ============================================================
// 参数输入行
// ============================================================

/**
 * 单个参数的输入行
 *
 * 根据参数类型自动选择合适的输入控件：
 * - boolean → Select (true/false)
 * - array/object → TextArea (JSON)
 * - string 且参数名含 path/file → Input + 上传按钮（选本地文件上传到 MinIO）
 * - 其他 → Input
 */
function ParamInputRow({
  param,
  value,
  onChange,
}: {
  param: ParamDef;
  value: unknown;
  onChange: (newValue: unknown) => void;
}): JSX.Element {
  const t = param.type.toLowerCase();
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 判断是否需要文件上传（参数名含 path/file 且为 string 类型）
  const needsFileUpload =
    (t === 'string') &&
    /path|file/i.test(param.name);

  // 处理文件上传
  const handleFileUpload = async (file: File): Promise<void> => {
    setUploading(true);
    try {
      const res = await apiUploadFile(file);
      // 以 artifact:{artifact_id} 格式填入参数值
      onChange(convertParamValue(`artifact:${res.artifact_id}`, param.type));
      message.success(`上传成功: ${res.filename} (${(res.size / 1024).toFixed(1)} KB)`);
    } catch (err) {
      message.error(`上传失败: ${extractApiError(err)}`);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ marginBottom: 2 }}>
        <Space size={4}>
          <Text strong style={{ fontSize: 13 }}>
            {param.name}
          </Text>
          {param.required && (
            <Text type="danger" style={{ fontSize: 11 }}>
              必填
            </Text>
          )}
          <Tag style={{ fontSize: 10, margin: 0 }}>{param.type}</Tag>
        </Space>
        {param.description && (
          <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
            {param.description}
          </Text>
        )}
      </div>

      {t === 'boolean' || t === 'bool' ? (
        <Select
          size="small"
          style={{ width: '100%' }}
          value={value !== undefined ? String(value) : undefined}
          onChange={(v: string) => onChange(v === 'true')}
          options={[
            { value: 'true', label: 'true' },
            { value: 'false', label: 'false' },
          ]}
          placeholder="选择..."
          allowClear
        />
      ) : isComplexType(param.type) ? (
        <Input.TextArea
          rows={2}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
          value={typeof value === 'string' ? value : JSON.stringify(value ?? param.default ?? '', null, 2)}
          onChange={(e) => onChange(e.target.value)}
          placeholder={
            param.default !== undefined
              ? JSON.stringify(param.default, null, 2)
              : `输入 ${param.type} 格式的 JSON`
          }
        />
      ) : needsFileUpload ? (
        /* 文件路径参数：输入框 + 上传按钮 */
        <>
        <div style={{ display: 'flex', gap: 4 }}>
          <Input
            size="small"
            style={{ flex: 1 }}
            value={String(value ?? param.default ?? '')}
            onChange={(e) => {
              const newValue = convertParamValue(e.target.value, param.type);
              onChange(newValue);
            }}
            placeholder={
              param.default !== undefined
                ? String(param.default)
                : `请输入 ${param.name} 或点击上传`
            }
          />
          <input
            type="file"
            ref={fileInputRef}
            style={{ display: 'none' }}
            onChange={(e) => {
              const selectedFile = e.target.files?.[0];
              if (selectedFile) {
                void handleFileUpload(selectedFile);
              }
              // 清空 input 值，使同一文件可重复选择
              e.target.value = '';
            }}
          />
          <Button
            size="small"
            loading={uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            上传
          </Button>
        </div>
        </>
      ) : (
        <Input
          size="small"
          value={String(value ?? param.default ?? '')}
          onChange={(e) => {
            const newValue = convertParamValue(e.target.value, param.type);
            onChange(newValue);
          }}
          placeholder={
            param.default !== undefined
              ? String(param.default)
              : `请输入 ${param.name}`
          }
        />
      )}
    </div>
  );
}

// ============================================================
// 文件浏览器弹窗
// ============================================================

// 文件浏览器弹窗（已弃用，改用文件上传方式）
// @ts-expect-error — 保留未使用的组件定义
function FileBrowserModal({
  open,
  onClose,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  onSelect: (path: string) => void;
}): JSX.Element {
  const [currentPath, setCurrentPath] = useState('');
  const [browseData, setBrowseData] = useState<BrowseResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const loadDir = async (path: string): Promise<void> => {
    setLoading(true);
    try {
      const data = await apiBrowseFiles(path || undefined);
      setBrowseData(data);
      setCurrentPath(data.current_path);
    } catch (err) {
      message.error(`浏览失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      void loadDir('');
    }
  }, [open]);

  const handleDirClick = (dirName: string): void => {
    const newPath = currentPath ? `${currentPath}/${dirName}` : dirName;
    void loadDir(newPath);
  };

  const handleParentClick = (): void => {
    if (browseData?.parent_path !== null && browseData?.parent_path !== undefined) {
      void loadDir(browseData.parent_path);
    }
  };

  // 选中文件时拼接完整路径（相对于浏览根目录的绝对路径）
  const handleFileSelect = (fileName: string): void => {
    const relPath = currentPath ? `${currentPath}/${fileName}` : fileName;
    // 拼接为服务器绝对路径
    onSelect(`/Users/shuipei/Desktop/snowSP/irip/${relPath}`);
  };

  return (
    <Modal
      title="选择文件"
      open={open}
      onCancel={onClose}
      footer={null}
      width={560}
    >
      {/* 当前路径 + 上级按钮 */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Button
          size="small"
          disabled={!browseData?.parent_path && currentPath === ''}
          onClick={handleParentClick}
        >
          上级
        </Button>
        <Text type="secondary" style={{ fontSize: 12, flex: 1, fontFamily: 'monospace' }}>
          /{currentPath || ''}
        </Text>
      </div>

      {/* 文件列表 */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin />
        </div>
      ) : (
        <div style={{ maxHeight: 360, overflow: 'auto', border: '1px solid #e8e8e8', borderRadius: 4 }}>
          {browseData?.items.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Text type="secondary">空目录</Text>
            </div>
          ) : (
            browseData?.items.map((item) => (
              <div
                key={item.name}
                style={{
                  padding: '6px 12px',
                  cursor: 'pointer',
                  borderBottom: '1px solid #f0f0f0',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = '#f5f5f5';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent';
                }}
                onClick={() => {
                  if (item.type === 'dir') {
                    handleDirClick(item.name);
                  } else {
                    handleFileSelect(item.name);
                  }
                }}
              >
                <Tag color={item.type === 'dir' ? 'blue' : 'default'} style={{ margin: 0, fontSize: 10 }}>
                  {item.type === 'dir' ? 'DIR' : 'FILE'}
                </Tag>
                <Text style={{ fontSize: 13 }}>{item.name}</Text>
                {item.size !== null && item.type === 'file' && (
                  <Text type="secondary" style={{ fontSize: 11, marginLeft: 'auto' }}>
                    {item.size > 1024 ? `${(item.size / 1024).toFixed(1)} KB` : `${item.size} B`}
                  </Text>
                )}
              </div>
            ))
          )}
        </div>
      )}

      <Text type="secondary" style={{ fontSize: 11, marginTop: 8, display: 'block' }}>
        点击目录进入，点击文件选择
      </Text>
    </Modal>
  );
}

// ============================================================
// 边编辑行
// ============================================================

/**
 * 单条边的可视化编辑行
 *
 * 布局：源节点 → 源端口 → 目标节点 → 目标端口 → 删除
 */
function EdgeEditorRow({
  edge,
  nodeIdOptions,
  getOutputPorts,
  getInputPorts,
  onUpdate,
  onRemove,
}: {
  edge: VisualEdge;
  nodeIdOptions: { value: string; label: string }[];
  getOutputPorts: (nodeId: string) => PortDef[];
  getInputPorts: (nodeId: string) => PortDef[];
  onUpdate: (updates: Partial<VisualEdge>) => void;
  onRemove: () => void;
}): JSX.Element {
  const sourcePorts = getOutputPorts(edge.source_node);
  const targetPorts = getInputPorts(edge.target_node);

  return (
    <div
      style={{
        display: 'flex',
        gap: 4,
        alignItems: 'center',
        marginBottom: 8,
        flexWrap: 'wrap',
      }}
    >
      <Select
        style={{ width: 90 }}
        placeholder="源节点"
        value={edge.source_node || undefined}
        onChange={(value: string) =>
          onUpdate({ source_node: value, source_port: '' })
        }
        options={nodeIdOptions}
        allowClear
      />
      <Text type="secondary">→</Text>
      {sourcePorts.length > 0 ? (
        <Select
          style={{ width: 110 }}
          placeholder="源端口"
          value={edge.source_port || undefined}
          onChange={(value: string) => onUpdate({ source_port: value })}
          options={sourcePorts.map((p) => ({ value: p.name, label: p.name }))}
          allowClear
        />
      ) : (
        <Input
          style={{ width: 110 }}
          placeholder="端口名"
          value={edge.source_port}
          onChange={(e) => onUpdate({ source_port: e.target.value })}
        />
      )}
      <Text type="secondary">→</Text>
      <Select
        style={{ width: 90 }}
        placeholder="目标节点"
        value={edge.target_node || undefined}
        onChange={(value: string) =>
          onUpdate({ target_node: value, target_port: '' })
        }
        options={nodeIdOptions}
        allowClear
      />
      <Text type="secondary">→</Text>
      {targetPorts.length > 0 ? (
        <Select
          style={{ width: 110 }}
          placeholder="目标端口"
          value={edge.target_port || undefined}
          onChange={(value: string) => onUpdate({ target_port: value })}
          options={targetPorts.map((p) => ({ value: p.name, label: p.name }))}
          allowClear
        />
      ) : (
        <Input
          style={{ width: 110 }}
          placeholder="端口名"
          value={edge.target_port}
          onChange={(e) => onUpdate({ target_port: e.target.value })}
        />
      )}
      <Button danger size="small" onClick={onRemove}>
        删除
      </Button>
    </div>
  );
}
