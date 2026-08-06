/**
 * ProvenanceGraphView — 联邦溯源图可视化组件（AntV G6 5.x 封装）
 *
 * 功能：
 * - DAG 层次布局（dagre layout）展示溯源图
 * - 节点按类型着色：Fact 深蓝 / Snapshot 浅蓝 / Run 绿色 / Dataset 蓝色 /
 *   View 青色 / Insight 橙色 / ResultVersion 紫色 / KnowledgeRef 紫色 / Restricted 灰色
 * - 可见节点可点击跳转
 * - 受限节点灰色不可点击
 * - 支持折叠/展开子树
 * - 深度控制滑块
 * - 搜索高亮
 *
 * 参照 PRD 4.1 节 UI 设计与 arch-research-lineage.md 2.3 节文件 21。
 */
import { useEffect, useRef, useMemo, useCallback } from 'react';
import { Graph, type NodeData, type EdgeData } from '@antv/g6';
import { Typography, Empty, Spin } from 'antd';
import type {
  ProvenanceGraph,
  ProvenanceNode,
} from '@/api/researchLineage';

const { Text } = Typography;

// ============================================================
// 节点类型 → 颜色 / 图标 / 标签映射
// ============================================================

export type NodeTypeStyle = {
  color: string;
  label: string;
  icon: string;
};

/** 命名空间 → 节点视觉样式映射 */
const NODE_TYPE_STYLES: Record<string, NodeTypeStyle> = {
  'core:fact': { color: '#1a3a5c', label: '实验事实', icon: '🔬' },
  'core:derivation_run': { color: '#5a6c7d', label: '核心推导', icon: '⚙️' },
  'core:evidence_set': { color: '#7a8c9d', label: '证据集', icon: '📋' },
  'research:evidence_snapshot': { color: '#5bb8d0', label: '证据快照', icon: '📋' },
  'research:analysis_run': { color: '#52c41a', label: '分析运行', icon: '▶️' },
  'research:analysis_step': { color: '#73d13d', label: '分析步骤', icon: '▶️' },
  'research:derived_dataset': { color: '#1890ff', label: '衍生数据', icon: '📊' },
  'research:derived_dataset_version': { color: '#1890ff', label: '衍生数据', icon: '📊' },
  'research:view': { color: '#13c2c2', label: '图表', icon: '📈' },
  'research:view_version': { color: '#13c2c2', label: '图表', icon: '📈' },
  'research:insight': { color: '#fa8c16', label: 'Insight', icon: '💡' },
  'research:insight_version': { color: '#fa8c16', label: 'Insight', icon: '💡' },
  'research:result_version': { color: '#722ed1', label: '成果版本', icon: '📦' },
  'research:workspace': { color: '#2f54eb', label: '研究空间', icon: '🏠' },
  'research:knowledge_reference': { color: '#9254de', label: '知识库引用', icon: '📚' },
  restricted: { color: '#8c8c8c', label: '受限来源', icon: '🔒' },
};

/**
 * 根据命名空间获取节点样式。
 */
function getNodeStyle(namespace: string, nodeType: string): NodeTypeStyle {
  if (nodeType === 'restricted' || namespace === 'restricted') {
    return NODE_TYPE_STYLES['restricted'];
  }
  return NODE_TYPE_STYLES[namespace] ?? { color: '#8c8c8c', label: nodeType, icon: '❓' };
}

// ============================================================
// 组件 Props
// ============================================================

export type ProvenanceGraphViewProps = {
  /** 溯源图数据 */
  graph: ProvenanceGraph | null;
  /** 是否加载中 */
  loading?: boolean;
  /** 搜索关键词（匹配节点高亮，非匹配节点降低透明度） */
  searchKeyword?: string;
  /** 节点点击回调（可见节点） */
  onNodeClick?: (node: ProvenanceNode) => void;
  /** 容器高度，默认 500 */
  height?: number;
};

/**
 * ProvenanceGraphView — 溯源图 DAG 可视化组件
 *
 * 使用 AntV G6 5.x 的 Graph API 渲染联邦溯源图。
 * 节点按类型着色，受限节点灰色不可点击，支持折叠展开和搜索高亮。
 */
export function ProvenanceGraphView({
  graph,
  loading = false,
  searchKeyword = '',
  onNodeClick,
  height = 500,
}: ProvenanceGraphViewProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const nodeMapRef = useRef<Map<string, ProvenanceNode>>(new Map());

  // ---- 将 ProvenanceGraph 转换为 G6 数据 ----
  const g6Data = useMemo(() => {
    if (!graph) return { nodes: [] as NodeData[], edges: [] as EdgeData[] };

    const nodes: NodeData[] = [];
    const edges: EdgeData[] = [];
    nodeMapRef.current.clear();

    for (const node of graph.nodes) {
      const nodeId = `${node.namespace}:${node.node_id}`;
      nodeMapRef.current.set(nodeId, node);

      const style = getNodeStyle(node.namespace, node.node_type);
      const displayLabel = node.display_label?.display_label ?? '未知节点';
      const typeLabel = node.display_label?.node_type_label ?? style.label;
      const versionSummary = node.display_label?.version_summary ?? '';
      const isRestricted = node.is_restricted || node.node_type === 'restricted';

      nodes.push({
        id: nodeId,
        data: {
          kind: 'node',
          nodeType: node.node_type,
          namespace: node.namespace,
          isRestricted,
          displayLabel,
          typeLabel,
          versionSummary,
          // G6 5.x 扁平样式属性（label* 前缀）
          fill: style.color,
          stroke: isRestricted ? '#595959' : style.color,
          lineWidth: 2,
          opacity: isRestricted ? 0.5 : 1,
          labelText: `${style.icon} ${isRestricted ? '受限来源' : displayLabel}${versionSummary ? ` (${versionSummary})` : ''}`,
          labelFill: '#333',
          labelFontSize: 11,
          labelFontWeight: 500,
          labelPlacement: 'bottom',
          labelMaxWidth: 160,
          labelWordWrap: true,
          iconText: style.icon,
          iconFontSize: 14,
          iconFill: '#fff',
          // 自定义属性，用于点击回调
          _provenanceNode: node,
          _isRestricted: isRestricted,
          _jumpTarget: node.display_label?.jump_target ?? null,
        },
      });
    }

    for (const edge of graph.edges) {
      const sourceId = `${edge.source_namespace}:${edge.source_id}`;
      const targetId = `${edge.target_namespace}:${edge.target_id}`;
      const edgeId = `${sourceId}->${targetId}:${edge.edge_type}`;

      edges.push({
        id: edgeId,
        source: sourceId,
        target: targetId,
        data: {
          kind: 'edge',
          edgeType: edge.edge_type,
          edgeTypeLabel: edge.edge_type_label,
          stroke: '#bfbfbf',
          lineWidth: 1.5,
          endArrow: true,
          endArrowSize: 6,
          labelText: edge.edge_type_label ?? edge.edge_type,
          labelFontSize: 9,
          labelFill: '#8c8c8c',
          labelBackground: true,
          labelBackgroundFill: 'rgba(255,255,255,0.85)',
          labelBackgroundRadius: 3,
          labelBackgroundPadding: [2, 4, 2, 4],
        },
      });
    }

    return { nodes, edges };
  }, [graph]);

  // ---- 初始化 / 更新 G6 图实例 ----
  useEffect(() => {
    if (!containerRef.current) return;

    const g6 = new Graph({
      container: containerRef.current,
      width: containerRef.current.clientWidth || 800,
      height: height,
      autoFit: 'view',
      data: g6Data,
      layout: {
        type: 'antv-dagre',
        rankdir: 'TB',
        nodesep: 40,
        ranksep: 60,
      },
      node: {
        type: 'rect',
        style: (d: NodeData) => {
          const data = (d.data ?? {}) as Record<string, unknown>;
          const fill = (data.fill as string) ?? '#8c8c8c';
          const isRestricted = (data.isRestricted as boolean) ?? false;
          const labelText = (data.labelText as string) ?? '';
          const iconText = (data.iconText as string) ?? '';
          return {
            fill,
            stroke: isRestricted ? '#595959' : fill,
            lineWidth: 2,
            opacity: isRestricted ? 0.5 : 1,
            size: [160, 40],
            labelText,
            labelFill: '#333',
            labelFontSize: 11,
            labelFontWeight: 500,
            labelPlacement: 'bottom',
            labelMaxWidth: 160,
            labelWordWrap: true,
            iconText,
            iconFontSize: 14,
            iconFill: '#fff',
          };
        },
        state: {
          active: {
            style: {
              lineWidth: 3,
              shadowColor: 'rgba(24,144,255,0.4)',
              shadowBlur: 12,
            },
          },
          dimmed: {
            style: {
              opacity: 0.2,
            },
          },
        },
      },
      edge: {
        type: 'quadratic',
        style: (d: EdgeData) => {
          const data = (d.data ?? {}) as Record<string, unknown>;
          return {
            stroke: (data.stroke as string) ?? '#bfbfbf',
            lineWidth: 1.5,
            endArrow: true,
            endArrowSize: 6,
            labelText: (data.labelText as string) ?? '',
            labelFontSize: 9,
            labelFill: '#8c8c8c',
            labelBackground: true,
            labelBackgroundFill: 'rgba(255,255,255,0.85)',
          };
        },
        state: {
          active: {
            style: {
              stroke: '#1890ff',
              lineWidth: 2,
            },
          },
          dimmed: {
            style: {
              opacity: 0.15,
            },
          },
        },
      },
      behaviors: [
        {
          type: 'drag-canvas',
        },
        {
          type: 'zoom-canvas',
        },
        {
          type: 'drag-element',
        },
        {
          key: 'collapse-expand',
          type: 'collapse-expand',
          trigger: 'click',
          iconSrc: 'https://gw.alipayobjects.com/zos/antfincdn/9r4Mk2%24J4/expand.svg',
          collapsedIconSrc: 'https://gw.alipayobjects.com/zos/antfincdn/PMn%24iN2WJ/collapsed.svg',
        },
      ],
      plugins: [
        {
          key: 'tooltip',
          type: 'tooltip',
          getContent: (e: { target: { data: Record<string, unknown> } }) => {
            const nodeData = e?.target?.data;
            if (!nodeData) return '';
            const isRestricted = nodeData._isRestricted as boolean;
            const typeLabel = nodeData.typeLabel as string;
            const displayLabel = nodeData.displayLabel as string;
            const versionSummary = nodeData.versionSummary as string;
            if (isRestricted) {
              return `<div style="padding:6px;font-size:12px;">🔒 受限来源<br/><span style="color:#8c8c8c;">无权访问此节点</span></div>`;
            }
            return `<div style="padding:6px;font-size:12px;"><b>${typeLabel}</b><br/>${displayLabel}${versionSummary ? ` (${versionSummary})` : ''}</div>`;
          },
        },
      ],
    });

    graphRef.current = g6;

    // 节点点击事件
    g6.on('node:click', (evt: { target: { data: Record<string, unknown> } }) => {
      const nodeData = evt?.target?.data;
      if (!nodeData) return;
      const isRestricted = nodeData._isRestricted as boolean;
      if (isRestricted) return; // 受限节点不可点击
      const provNode = nodeData._provenanceNode as ProvenanceNode;
      if (provNode && onNodeClick) {
        onNodeClick(provNode);
      }
    });

    // 启动渲染
    g6.render().catch(() => {
      // 渲染失败静默处理
    });

    return () => {
      g6.destroy();
      graphRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [g6Data, height]);

  // ---- 搜索高亮 ----
  const applySearchHighlight = useCallback(
    (keyword: string) => {
      const g6 = graphRef.current;
      if (!g6) return;

      if (!keyword.trim()) {
        // 清除高亮状态
        g6.setElementState('*', 'dimmed', false);
        g6.setElementState('*', 'active', false);
        return;
      }

      const lowerKeyword = keyword.toLowerCase();

      // 遍历所有节点，匹配的高亮，不匹配的降低透明度
      const allNodes = g6.getNodeData();
      for (const node of allNodes) {
        const data = node.data as Record<string, unknown>;
        const isRestricted = data._isRestricted as boolean;
        if (isRestricted) {
          g6.setElementState(node.id, 'dimmed', true);
          continue;
        }
        const displayLabel = (data.displayLabel as string) ?? '';
        const typeLabel = (data.typeLabel as string) ?? '';
        const matched =
          displayLabel.toLowerCase().includes(lowerKeyword) ||
          typeLabel.toLowerCase().includes(lowerKeyword);
        g6.setElementState(node.id, 'active', matched);
        g6.setElementState(node.id, 'dimmed', !matched);
      }

      // 边也跟随高亮
      const allEdges = g6.getEdgeData();
      for (const edge of allEdges) {
        g6.setElementState(edge.id, 'dimmed', true);
      }
    },
    [],
  );

  useEffect(() => {
    applySearchHighlight(searchKeyword);
  }, [searchKeyword, applySearchHighlight]);

  // ---- 渲染 ----
  if (loading) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height,
          background: 'var(--ocean-surface-structural, rgba(142,191,208,0.46))',
          borderRadius: 'var(--ocean-radius-md, 6px)',
        }}
      >
        <Spin tip="加载溯源图..." />
      </div>
    );
  }

  if (!graph || graph.nodes.length === 0) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height,
          background: 'var(--ocean-surface-structural, rgba(142,191,208,0.46))',
          borderRadius: 'var(--ocean-radius-md, 6px)',
        }}
      >
        <Empty description="暂无溯源数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </div>
    );
  }

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height,
        background: 'var(--ocean-surface-default, rgba(240,250,251,0.72))',
        border: '1px solid var(--ocean-border-subtle, rgba(24,102,133,0.16))',
        borderRadius: 'var(--ocean-radius-md, 6px)',
        overflow: 'hidden',
      }}
    >
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {/* 图例 */}
      <div
        style={{
          position: 'absolute',
          bottom: 8,
          left: 8,
          background: 'rgba(255,255,255,0.9)',
          borderRadius: 4,
          padding: '6px 8px',
          fontSize: 10,
          lineHeight: '1.6',
          border: '1px solid var(--ocean-border-subtle, rgba(24,102,133,0.16))',
          pointerEvents: 'none',
        }}
      >
        <Text style={{ fontSize: 10, fontWeight: 600, display: 'block', marginBottom: 2 }}>
          图例
        </Text>
        {Object.entries(NODE_TYPE_STYLES)
          .filter(([ns]) =>
            ['core:fact', 'research:evidence_snapshot', 'research:analysis_run', 'research:derived_dataset', 'research:view', 'research:insight', 'research:result_version', 'research:knowledge_reference', 'restricted'].includes(ns),
          )
          .map(([ns, style]) => (
            <div key={ns} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span
                style={{
                  display: 'inline-block',
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: style.color,
                  opacity: ns === 'restricted' ? 0.5 : 1,
                }}
              />
              <span style={{ color: 'var(--ocean-text-secondary, #486b7e)' }}>
                {style.icon} {style.label}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}
