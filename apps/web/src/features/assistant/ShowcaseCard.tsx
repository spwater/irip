/**
 * 橱窗卡片组件。
 *
 * 单张橱窗卡片，展示类型图标 + 标题（可编辑）+ 缩略预览 + 数据来源 + 操作入口。
 * 支持：展开全屏 Modal、定位原文、删除（二次确认）、重命名（双击编辑）。
 */
import { useState, useRef, useEffect, useMemo } from 'react';
import {
  Button,
  Input,
  Modal,
  Popconfirm,
  Tag,
  Tooltip,
  Typography,
  type InputRef,
} from 'antd';
import {
  BarChartOutlined,
  TableOutlined,
  BulbOutlined,
  FunctionOutlined,
  FileTextOutlined,
  ExpandOutlined,
  AimOutlined,
  DeleteOutlined,
  EditOutlined,
  CheckOutlined,
  CloseOutlined,
} from '@ant-design/icons';
import type { ShowcaseItem, ShowcaseBlockType } from '@/api/showcase';
import { PlotlyBlock } from '@/features/assistant/PlotlyBlock';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import katex from 'katex';
import 'katex/dist/katex.min.css';

const { Text } = Typography;

/** 用 KaTeX JS API 渲染公式（绕开 rehype-katex） */
function renderMath(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex, { displayMode, throwOnError: false, strict: false });
  } catch {
    return `<span style="color:red">${tex}</span>`;
  }
}

/** 预处理公式 → 占位符 */
function preprocessMath(md: string): { html: string; mathMap: Map<string, string> } {
  const mathMap = new Map<string, string>();
  let counter = 0;
  let result = md;
  result = result.replace(/\$\$([\s\S]*?)\$\$/g, (_, tex: string) => {
    const html = renderMath(tex.trim(), true);
    const placeholder = `MATHDISPLAY${counter}MATHEND`;
    mathMap.set(placeholder, html);
    counter++;
    return placeholder;
  });
  result = result.replace(/\$([^\n$]+?)\$/g, (_, tex: string) => {
    const html = renderMath(tex.trim(), false);
    const placeholder = `MATHINLINE${counter}MATHEND`;
    mathMap.set(placeholder, html);
    counter++;
    return placeholder;
  });
  return { html: result, mathMap };
}

/** 替换占位符为 KaTeX HTML */
function replacePlaceholders(text: string, mathMap: Map<string, string>): React.ReactNode {
  if (!text.includes('MATH')) return text;
  const parts: React.ReactNode[] = [];
  const regex = /(MATH(?:DISPLAY|INLINE)\d+MATHEND)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
    const html = mathMap.get(match[1]);
    if (html) parts.push(<span key={`math-${key}`} dangerouslySetInnerHTML={{ __html: html }} />);
    else parts.push(match[1]);
    lastIndex = match.index + match[1].length;
    key++;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts.length === 1 ? parts[0] : <>{parts}</>;
}

/** 块类型 → 图标映射 */
const BLOCK_ICONS: Record<ShowcaseBlockType, JSX.Element> = {
  echarts: <BarChartOutlined />,
  plotly: <BarChartOutlined />,
  table: <TableOutlined />,
  conclusion: <BulbOutlined />,
  formula: <FunctionOutlined />,
  text: <FileTextOutlined />,
};

/** 块类型 → Tag 颜色映射 */
const BLOCK_COLORS: Record<ShowcaseBlockType, string> = {
  echarts: 'blue',
  plotly: 'purple',
  table: 'cyan',
  conclusion: 'gold',
  formula: 'green',
  text: 'default',
};

/** 块类型 → 中文标签 */
const BLOCK_LABELS: Record<ShowcaseBlockType, string> = {
  echarts: '图表',
  plotly: '图表',
  table: '表格',
  conclusion: '结论',
  formula: '公式',
  text: '文本',
};

export function ShowcaseCard({
  item,
  onLocate,
  onDelete,
  onRename,
}: {
  /** 橱窗卡片数据 */
  item: ShowcaseItem;
  /** 定位原文回调 */
  onLocate: (messageId: string, blockIndex: number) => void;
  /** 删除回调 */
  onDelete: (itemId: string) => void;
  /** 重命名回调 */
  onRename: (itemId: string, title: string) => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(item.title);
  const inputRef = useRef<InputRef | null>(null);

  // 同步外部 title 变更
  useEffect(() => {
    if (!editing) {
      setEditTitle(item.title);
    }
  }, [item.title, editing]);

  // 编辑模式自动聚焦
  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
    }
  }, [editing]);

  const handleSaveTitle = (): void => {
    const trimmed = editTitle.trim();
    if (trimmed && trimmed !== item.title) {
      onRename(item.id, trimmed);
    }
    setEditing(false);
  };

  const handleCancelEdit = (): void => {
    setEditTitle(item.title);
    setEditing(false);
  };

  // 缩略预览渲染
  const renderThumbnail = (): JSX.Element => {
    const snapshot = item.content_snapshot;
    switch (item.block_type) {
      case 'echarts':
        // ECharts 缩略图：复用渲染组件，高度 120
        return <EChartsThumbnail optionStr={snapshot} />;
      case 'plotly':
        // Plotly 缩略图不渲染三维图，只显示类型标签提示（展开后可看完整图）
        return (
          <div style={{ height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 4 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Plotly 图表
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 11, opacity: 0.6 }}>
              点击展开查看
            </Typography.Text>
          </div>
        );
      case 'table':
        // 表格前 3 行
        return (
          <div
            style={{
              maxHeight: 120,
              overflow: 'hidden',
              fontSize: 12,
            }}
            className="ai-markdown-body"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {truncateTable(snapshot, 3)}
            </ReactMarkdown>
          </div>
        );
      case 'conclusion':
      case 'text':
        // 文本/结论类：用 Markdown 渲染（支持公式），截取前几行
        return (
          <div
            style={{
              maxHeight: 120,
              overflow: 'hidden',
              fontSize: 12,
            }}
            className="ai-markdown-body"
          >
            <ShowcaseMarkdown content={snapshot.split('\n').slice(0, 4).join('\n')} />
          </div>
        );
      case 'formula':
        // 公式类：用 Markdown + KaTeX 渲染完整公式
        return (
          <div
            style={{
              maxHeight: 120,
              overflow: 'hidden',
              fontSize: 12,
            }}
            className="ai-markdown-body"
          >
            <ShowcaseMarkdown content={snapshot} />
          </div>
        );
      default:
        return (
          <Text
            style={{ fontSize: 12, color: 'var(--ocean-text-muted)' }}
            ellipsis
          >
            {snapshot.split('\n').slice(0, 2).join('\n')}
          </Text>
        );
    }
  };

  // 数据来源摘要
  const ds = item.data_source;
  const dsSummary = [
    ds.sample_labels.length > 0 ? ds.sample_labels.join(', ') : '',
    ds.task_name ? `任务: ${ds.task_name}` : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <>
      {/* KaTeX CSS 通过 main.tsx import 引入 */}
      <div
        style={{
          border: '1px solid var(--ocean-border-subtle)',
          borderRadius: 8,
          padding: 8,
          background: 'var(--ocean-surface-strong, #fff)',
        }}
      >
        {/* 顶部：类型图标 + 标题（可编辑）+ 类型 Tag */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            marginBottom: 4,
          }}
        >
          <span style={{ color: 'var(--ocean-text-muted)', fontSize: 14 }}>
            {BLOCK_ICONS[item.block_type] ?? <FileTextOutlined />}
          </span>
          {editing ? (
            <Input
              ref={inputRef}
              size="small"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onPressEnter={handleSaveTitle}
              suffix={
                <>
                  <Button
                    size="small"
                    type="text"
                    icon={<CheckOutlined />}
                    onClick={handleSaveTitle}
                  />
                  <Button
                    size="small"
                    type="text"
                    icon={<CloseOutlined />}
                    onClick={handleCancelEdit}
                  />
                </>
              }
              style={{ flex: 1, minWidth: 0 }}
            />
          ) : (
            <Text
              strong
              style={{ flex: 1, fontSize: 13, minWidth: 0 }}
              ellipsis
              onDoubleClick={() => setEditing(true)}
              title="双击编辑标题"
            >
              {item.title || BLOCK_LABELS[item.block_type]}
            </Text>
          )}
          <Tag
            color={BLOCK_COLORS[item.block_type] ?? 'default'}
            style={{ fontSize: 10, margin: 0, flexShrink: 0 }}
          >
            {BLOCK_LABELS[item.block_type] ?? item.block_type}
          </Tag>
        </div>

        {/* 中部：缩略预览 */}
        <div style={{ margin: '4px 0' }}>{renderThumbnail()}</div>

        {/* 底部：数据来源 + 创建时间 */}
        <div style={{ marginTop: 4 }}>
          {dsSummary && (
            <Text
              style={{ fontSize: 11, color: 'var(--ocean-text-muted)' }}
              ellipsis
            >
              来源: {dsSummary}
            </Text>
          )}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginTop: 2,
            }}
          >
            <Text style={{ fontSize: 10, color: 'var(--ocean-text-muted)' }}>
              {new Date(item.created_at).toLocaleString('zh-CN', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
              })}
            </Text>
            {/* 操作区 */}
            <div style={{ display: 'flex', gap: 2 }}>
              <Tooltip title="编辑标题">
                <Button
                  size="small"
                  type="text"
                  icon={<EditOutlined />}
                  onClick={() => setEditing(true)}
                  style={{ padding: '0 4px', fontSize: 12 }}
                />
              </Tooltip>
              <Tooltip title="展开">
                <Button
                  size="small"
                  type="text"
                  icon={<ExpandOutlined />}
                  onClick={() => setExpanded(true)}
                  style={{ padding: '0 4px', fontSize: 12 }}
                />
              </Tooltip>
              <Tooltip title="定位原文">
                <Button
                  size="small"
                  type="text"
                  icon={<AimOutlined />}
                  onClick={() =>
                    onLocate(item.source_message_id, item.source_block_index)
                  }
                  style={{ padding: '0 4px', fontSize: 12 }}
                />
              </Tooltip>
              <Popconfirm
                title="确认删除此卡片？"
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => onDelete(item.id)}
              >
                <Button
                  size="small"
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  style={{ padding: '0 4px', fontSize: 12 }}
                />
              </Popconfirm>
            </div>
          </div>
        </div>
      </div>

      {/* 展开 Modal */}
      <Modal
        title={item.title || BLOCK_LABELS[item.block_type]}
        open={expanded}
        onCancel={() => setExpanded(false)}
        footer={null}
        width={800}
      >
        <div className="ai-markdown-body">
          {item.block_type === 'plotly' ? (
            <PlotlyBlock optionStr={item.content_snapshot} height={500} />
          ) : item.block_type === 'echarts' ? (
            <EchartsFull optionStr={item.content_snapshot} />
          ) : (
            <ShowcaseMarkdown content={item.content_snapshot} />
          )}
        </div>
      </Modal>
    </>
  );
}

/** 橱窗展开内容渲染：用 KaTeX JS API 渲染公式 + remarkGfm 渲染表格 */
function ShowcaseMarkdown({ content }: { content: string }): JSX.Element {
  const { html: preprocessed, mathMap } = useMemo(() => preprocessMath(content), [content]);

  const components = useMemo(() => ({
    p: ({ children }: { children?: React.ReactNode }) => {
      if (typeof children === 'string' && children.includes('MATH')) {
        return <p>{replacePlaceholders(children, mathMap)}</p>;
      }
      if (Array.isArray(children)) {
        const hasMath = children.some(c => typeof c === 'string' && c.includes('MATH'));
        if (hasMath) {
          return <p>{children.map(c => typeof c === 'string' && c.includes('MATH') ? replacePlaceholders(c, mathMap) : c)}</p>;
        }
      }
      return <p>{children}</p>;
    },
  }), [mathMap]);

  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {preprocessed}
    </ReactMarkdown>
  );
}

/**
 * 截断 Markdown 表格，只保留前 n 行。
 */
function truncateTable(md: string, maxRows: number): string {
  const lines = md.split('\n');
  // 找到表格起始行（含 | 的行）
  const tableLines: string[] = [];
  for (const line of lines) {
    if (line.includes('|')) {
      tableLines.push(line);
      if (tableLines.length >= maxRows + 2) break; // 表头 + 分隔行 + 数据行
    } else if (tableLines.length > 0) {
      break;
    }
  }
  return tableLines.join('\n');
}

/**
 * ECharts 缩略图（复用动态加载逻辑，高度 120）。
 *
 * 缩略图模式下注入小字体配置，避免 120px 高度内图表文字拥挤压缩。
 */
function EChartsThumbnail({ optionStr }: { optionStr: string }): JSX.Element {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<{ dispose: () => void; resize: () => void } | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    let cancelled = false;

    // 宽松解析
    let parsed: Record<string, unknown> | null = null;
    try {
      parsed = JSON.parse(optionStr);
    } catch {
      try {
        const lenient = optionStr
          .replace(/([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)/g, '$1"$2"$3')
          .replace(/'/g, '"')
          .replace(/,(\s*[}\]])/g, '$1');
        parsed = JSON.parse(lenient);
      } catch {
        parsed = null;
      }
    }

    if (!parsed) return;

    // 缩略图小字体注入：覆盖 axisLabel / nameTextStyle / legend / title 字体大小
    const SMALL_FONT = 8;
    const injectSmallFont = (axis: unknown): unknown => {
      if (Array.isArray(axis)) return axis.map((a) => {
        const obj = a as Record<string, unknown>;
        return { ...obj, axisLabel: { ...(obj.axisLabel as Record<string, unknown> | undefined), fontSize: SMALL_FONT }, nameTextStyle: { fontSize: SMALL_FONT } };
      });
      if (axis && typeof axis === 'object') {
        const obj = axis as Record<string, unknown>;
        return { ...obj, axisLabel: { ...(obj.axisLabel as Record<string, unknown> | undefined), fontSize: SMALL_FONT }, nameTextStyle: { fontSize: SMALL_FONT } };
      }
      return axis;
    };
    const thumbnailOption: Record<string, unknown> = {
      ...parsed,
      title: { ...(parsed.title as Record<string, unknown> | undefined), textStyle: { fontSize: 10 } },
      legend: { ...(parsed.legend as Record<string, unknown> | undefined), textStyle: { fontSize: SMALL_FONT }, itemWidth: 8, itemHeight: 8 },
      xAxis: injectSmallFont(parsed.xAxis),
      yAxis: injectSmallFont(parsed.yAxis),
      grid: { ...(parsed.grid as Record<string, unknown> | undefined), top: 15, bottom: 18, left: 28, right: 8, containLabel: true },
    };

    import('echarts').then((echarts) => {
      if (cancelled || !chartRef.current) return;
      if (chartInstanceRef.current) {
        chartInstanceRef.current.dispose();
      }
      const chart = echarts.init(chartRef.current, undefined, {
        width: chartRef.current.clientWidth || 300,
        height: 120,
      });
      chart.setOption(thumbnailOption);
      chartInstanceRef.current = chart;
    });

    return () => {
      cancelled = true;
      if (chartInstanceRef.current) {
        chartInstanceRef.current.dispose();
        chartInstanceRef.current = null;
      }
    };
  }, [optionStr]);

  return <div ref={chartRef} style={{ width: '100%', height: 120 }} />;
}

/**
 * ECharts 全尺寸图表（展开 Modal 用）。
 */
function EchartsFull({ optionStr }: { optionStr: string }): JSX.Element {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<{ dispose: () => void; resize: () => void } | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;

    let parsed: Record<string, unknown> | null = null;
    try {
      parsed = JSON.parse(optionStr);
    } catch {
      try {
        const lenient = optionStr
          .replace(/([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)/g, '$1"$2"$3')
          .replace(/'/g, '"')
          .replace(/,(\s*[}\]])/g, '$1');
        parsed = JSON.parse(lenient);
      } catch {
        parsed = null;
      }
    }

    if (!parsed) return;

    import('echarts').then((echarts) => {
      if (cancelled || !chartRef.current) return;
      if (chartInstanceRef.current) {
        chartInstanceRef.current.dispose();
      }
      const width = chartRef.current.clientWidth || 700;
      const chart = echarts.init(chartRef.current, undefined, {
        width,
        height: 500,
      });
      chart.setOption(parsed);
      chartInstanceRef.current = chart;

      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(() => {
          if (!cancelled && chartRef.current) {
            chart.resize();
          }
        });
        resizeObserver.observe(chartRef.current);
      }
    });

    return () => {
      cancelled = true;
      if (resizeObserver) {
        resizeObserver.disconnect();
      }
      if (chartInstanceRef.current) {
        chartInstanceRef.current.dispose();
        chartInstanceRef.current = null;
      }
    };
  }, [optionStr]);

  return <div ref={chartRef} style={{ width: '100%', height: 500 }} />;
}

export default ShowcaseCard;
