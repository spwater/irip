/**
 * ProvenanceTab — 溯源 Tab 组件（封装 ProvenanceGraphView + 查询选项面板）
 *
 * 可用于成果详情页"数据溯源"Tab（ResultProvenanceTab）
 * 和产物详情页"数据溯源"区域（ProductProvenanceSection）。
 *
 * 功能：
 * - 调用溯源图查询 API 获取 ProvenanceGraph
 * - 渲染 ProvenanceGraphView（DAG 可视化）
 * - 渲染 ProvenanceControls（深度控制 / 搜索 / 导出）
 * - 渲染 ProvenanceStats（节点统计摘要）
 * - 深度变化时重新查询
 * - 导出 JSON / PNG
 *
 * 参照 PRD 4.1 节 UI 设计与 arch-research-lineage.md 2.3 节文件 26/27。
 */
import { useState, useEffect, useCallback } from 'react';
import { Space, Typography, Button, Slider, Input, Tag, message } from 'antd';
import {
  DownloadOutlined,
  FileTextOutlined,
  SearchOutlined,
  NodeIndexOutlined,
  LockOutlined,
} from '@ant-design/icons';
import { ProvenanceGraphView } from './ProvenanceGraphView';
import type {
  ProvenanceGraph,
  ProvenanceNode,
} from '@/api/researchLineage';

const { Text } = Typography;

// ============================================================
// Props
// ============================================================

export type ProvenanceTabProps = {
  /** 获取溯源图的异步函数 */
  fetchGraph: (maxDepth: number) => Promise<ProvenanceGraph>;
  /** 导出溯源图的异步函数（可选，默认使用通用导出端点） */
  exportGraph?: (format: 'json' | 'png') => Promise<void>;
  /** 标题前缀，如 "成果版本溯源" 或 "产物溯源" */
  title?: string;
  /** 容器高度 */
  height?: number;
};

// ============================================================
// 节点类型中文标签（用于统计摘要）
// ============================================================

const NODE_TYPE_LABELS: Record<string, string> = {
  'core:fact': '实验',
  'core:derivation_run': '推导',
  'core:evidence_set': '证据集',
  'research:evidence_snapshot': '快照',
  'research:analysis_run': '运行',
  'research:analysis_step': '步骤',
  'research:derived_dataset': '数据',
  'research:derived_dataset_version': '数据',
  'research:view': '图表',
  'research:view_version': '图表',
  'research:insight': 'Insight',
  'research:insight_version': 'Insight',
  'research:result_version': '成果',
  'research:workspace': '空间',
  'research:knowledge_reference': '知识库',
  restricted: '受限',
};

/**
 * ProvenanceTab — 溯源图 Tab 组件
 */
export function ProvenanceTab({
  fetchGraph,
  exportGraph,
  height = 500,
}: ProvenanceTabProps): JSX.Element {
  const [graph, setGraph] = useState<ProvenanceGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [maxDepth, setMaxDepth] = useState(5);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [exporting, setExporting] = useState<'json' | 'png' | null>(null);

  // ---- 查询溯源图 ----
  const loadGraph = useCallback(
    async (depth: number) => {
      setLoading(true);
      try {
        const g = await fetchGraph(depth);
        setGraph(g);
      } catch {
        message.error('加载溯源图失败');
        setGraph(null);
      } finally {
        setLoading(false);
      }
    },
    [fetchGraph],
  );

  useEffect(() => {
    void loadGraph(maxDepth);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- 深度滑块变化时重新查询 ----
  const handleDepthChange = useCallback(
    (value: number | number[]) => {
      const depth = Array.isArray(value) ? value[0] : value;
      setMaxDepth(depth);
      void loadGraph(depth);
    },
    [loadGraph],
  );

  // ---- 导出 ----
  const handleExport = useCallback(
    async (format: 'json' | 'png') => {
      if (exportGraph) {
        setExporting(format);
        try {
          await exportGraph(format);
          message.success(`已导出 ${format.toUpperCase()}`);
        } catch {
          message.error(`导出失败`);
        } finally {
          setExporting(null);
        }
        return;
      }
      // 默认导出逻辑：下载 JSON 数据
      if (!graph) return;
      setExporting(format);
      try {
        if (format === 'json') {
          const blob = new Blob([JSON.stringify(graph, null, 2)], {
            type: 'application/json',
          });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `provenance-graph-${Date.now()}.json`;
          a.click();
          URL.revokeObjectURL(url);
          message.success('已导出 JSON');
        } else {
          message.info('PNG 导出请使用截图工具');
        }
      } finally {
        setExporting(null);
      }
    },
    [graph, exportGraph],
  );

  // ---- 节点点击回调 ----
  const handleNodeClick = useCallback((node: ProvenanceNode) => {
    const jumpTarget = node.display_label?.jump_target;
    if (jumpTarget) {
      // 如果有跳转目标，打开新窗口
      window.open(jumpTarget, '_blank');
    } else {
      // 否则显示节点信息
      message.info(
        `${node.display_label?.node_type_label ?? node.node_type}: ${node.display_label?.display_label ?? '未知'}`,
      );
    }
  }, []);

  // ---- 统计信息 ----
  const stats = graph?.stats;
  const nodesByType = stats?.nodes_by_type ?? {};

  return (
    <div style={{ width: '100%' }}>
      {/* 统计摘要栏 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '8px 12px',
          marginBottom: 8,
          background: 'var(--ocean-surface-structural, rgba(142,191,208,0.46))',
          borderRadius: 'var(--ocean-radius-md, 6px)',
          flexWrap: 'wrap',
        }}
      >
        <Space size={4}>
          <NodeIndexOutlined style={{ color: 'var(--ocean-current-bright, #17b8ce)' }} />
          <Text strong style={{ fontSize: 13 }}>
            节点统计:
          </Text>
          <Tag>{`总 ${stats?.total_nodes ?? 0}`}</Tag>
          {Object.entries(nodesByType).map(([type, count]) => {
            const label = NODE_TYPE_LABELS[type] ?? type;
            if (type === 'restricted') {
              return (
                <Tag key={type} color="default" icon={<LockOutlined />}>
                  {`${label} ${count}`}
                </Tag>
              );
            }
            return (
              <Tag key={type} color="blue">
                {`${label} ${count}`}
              </Tag>
            );
          })}
          {(stats?.restricted_nodes_count ?? 0) > 0 && !nodesByType['restricted'] && (
            <Tag color="default" icon={<LockOutlined />}>
              {`受限 ${stats?.restricted_nodes_count}`}
            </Tag>
          )}
          {(stats?.truncated_count ?? 0) > 0 && (
            <Tag color="warning">{`已截断 ${stats?.truncated_count}`}</Tag>
          )}
        </Space>
      </div>

      {/* 控制栏 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '8px 12px',
          marginBottom: 8,
          background: 'var(--ocean-surface-default, rgba(240,250,251,0.72))',
          border: '1px solid var(--ocean-border-subtle, rgba(24,102,133,0.16))',
          borderRadius: 'var(--ocean-radius-md, 6px)',
          flexWrap: 'wrap',
        }}
      >
        {/* 深度控制 */}
        <Space size={6}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            深度:
          </Text>
          <Slider
            min={1}
            max={20}
            value={maxDepth}
            onChange={handleDepthChange}
            style={{ width: 120 }}
            tooltip={{ formatter: (v) => `${v} 层` }}
          />
          <Tag style={{ fontSize: 11 }}>{`${maxDepth}层`}</Tag>
        </Space>

        {/* 搜索 */}
        <Input
          prefix={<SearchOutlined style={{ fontSize: 12, color: '#bfbfbf' }} />}
          placeholder="搜索节点..."
          allowClear
          value={searchKeyword}
          onChange={(e) => setSearchKeyword(e.target.value)}
          style={{ width: 180, fontSize: 12 }}
        />

        {/* 导出 */}
        <Space size={4}>
          <Button
            size="small"
            icon={<FileTextOutlined />}
            loading={exporting === 'json'}
            onClick={() => handleExport('json')}
          >
            导出 JSON
          </Button>
          <Button
            size="small"
            icon={<DownloadOutlined />}
            loading={exporting === 'png'}
            onClick={() => handleExport('png')}
          >
            导出 PNG
          </Button>
        </Space>
      </div>

      {/* 溯源图 */}
      <ProvenanceGraphView
        graph={graph}
        loading={loading}
        searchKeyword={searchKeyword}
        onNodeClick={handleNodeClick}
        height={height}
      />
    </div>
  );
}
