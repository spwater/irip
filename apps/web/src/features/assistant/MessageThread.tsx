import { Avatar, Typography } from 'antd';
import CitationList from '@/features/assistant/CitationList';
import ToolTrace from '@/features/assistant/ToolTrace';
import type { AssistantMessage, Citation, ToolCallSummary } from '@/api/models-ai';
import 'katex/dist/katex.min.css';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import { BlockWrapper } from '@/features/assistant/BlockWrapper';
import { PlotlyBlock } from '@/features/assistant/PlotlyBlock';

const { Text, Paragraph } = Typography;

// KaTeX style for proper rendering within react-markdown
const katexStyle = `
.ai-markdown-body .katex { font-size: 1.05em; }
.ai-markdown-body .katex-display { overflow-x: auto; overflow-y: hidden; margin: 8px 0; }
.ai-markdown-body .katex-display::-webkit-scrollbar { height: 4px; }
.ai-markdown-body .katex * { box-sizing: content-box !important; }
`;

/**
 * Custom sanitize schema for rehype-sanitize.
 *
 * Extends the default schema to:
 * - Allow katex-related class names (added by rehype-katex)
 * - Allow data-* attributes used by katex
 * - Restrict protocols to http/https/data only (no javascript:)
 */
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    div: [
      ...(defaultSchema.attributes?.div || []),
      'className',
      'dataLatex',
      'dataDisplay',
    ],
    span: [
      ...(defaultSchema.attributes?.span || []),
      'className',
    ],
    code: [
      ...(defaultSchema.attributes?.code || []),
      'className',
    ],
    pre: [
      ...(defaultSchema.attributes?.pre || []),
      'className',
    ],
  },
  protocols: {
    ...defaultSchema.protocols,
    src: ['http', 'https', 'data'],
    href: ['http', 'https'],
  },
};

/**
 * 从 React 节点中提取纯文本内容（用于 content_snapshot）。
 */
function extractTextFromNode(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string') return node;
  if (typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractTextFromNode).join('');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (typeof node === 'object' && node !== null && 'props' in (node as any)) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const props = (node as any).props as { children?: ReactNode };
    return extractTextFromNode(props.children);
  }
  return '';
}

/**
 * ECharts chart block component.
 *
 * H-14: ECharts data is parsed independently, not through Markdown rendering.
 * The option JSON is parsed safely with JSON.parse and rendered via echarts.
 *
 * L-03 整改：
 * - ECharts 实例保存到 ref，避免每次 render 重复创建
 * - useEffect 依赖数组收敛为 [parsed]
 * - cleanup 函数可靠调用 chart.dispose()，防止内存泄漏
 * - 使用 cancelled 标志处理异步竞态（import 完成前组件已卸载）
 * - 使用 ResizeObserver 监听容器尺寸变化，自动 resize 图表
 * - 长对话反复进入后实例数和内存稳定
 */
function ChartBlock({ optionStr }: { optionStr: string }): JSX.Element {
  const chartRef = useRef<HTMLDivElement>(null);
  // L-03: 保存 ECharts 实例到 ref，cleanup 时 dispose
  const chartInstanceRef = useRef<{ dispose: () => void; resize: () => void } | null>(null);

  // 防抖：流式传输中不立即渲染，等内容稳定（300ms 无变化）后再渲染
  const [debouncedStr, setDebouncedStr] = useState(optionStr);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedStr(optionStr), 300);
    return () => clearTimeout(timer);
  }, [optionStr]);

  const parsed = useMemo(() => {
    // 1. 先尝试标准 JSON.parse
    try {
      return JSON.parse(debouncedStr);
    } catch {
      // fall through to lenient parser
    }
    // 2. 宽松解析：将 JS 对象语法转为合法 JSON
    //    - 无引号的 key: title: → "title":
    //    - 单引号字符串: 'xxx' → "xxx"
    //    - 尾逗号: ,} → }, ,,] → ]
    try {
      const lenient = debouncedStr
        .replace(/([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)/g, '$1"$2"$3')
        .replace(/'/g, '"')
        .replace(/,(\s*[}\]])/g, '$1');
      return JSON.parse(lenient);
    } catch {
      return null;
    }
  }, [debouncedStr]);

  useEffect(() => {
    if (!parsed || !chartRef.current) return;

    // L-03: 异步取消标志，防止 import 完成前组件已卸载时创建孤儿实例
    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;

    // Find parent container width
    let width = 500;
    let el: HTMLElement | null = chartRef.current.parentElement;
    while (el) {
      if (el.clientWidth > 0) {
        width = el.clientWidth - 32;
        break;
      }
      el = el.parentElement;
    }
    if (width < 200) width = 500;

    // Enhance option with safe defaults
    const safeOption = { ...parsed };
    if (!safeOption.grid) safeOption.grid = {};
    safeOption.grid.containLabel = true;
    if (safeOption.xAxis && !Array.isArray(safeOption.xAxis)) {
      safeOption.xAxis.nameLocation = 'middle';
      safeOption.xAxis.nameGap = 25;
    }

    import('echarts').then((echarts) => {
      // L-03: 组件已卸载则不再创建实例
      if (cancelled || !chartRef.current) return;

      // L-03: 先 dispose 旧实例（如果有），防止重复创建
      if (chartInstanceRef.current) {
        chartInstanceRef.current.dispose();
        chartInstanceRef.current = null;
      }

      const chart = echarts.init(chartRef.current, undefined, { width, height: 400 });
      chart.setOption(safeOption);
      chartInstanceRef.current = chart;

      // L-03: ResizeObserver 监听容器尺寸变化
      if (chartRef.current && typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(() => {
          if (!cancelled && chartRef.current) {
            chart.resize();
          }
        });
        resizeObserver.observe(chartRef.current);
      }

      // Add copy button (L-01: 使用语义 button 元素，支持键盘)
      if (chartRef.current && !chartRef.current.querySelector('.echarts-copy-btn')) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'echarts-copy-btn';
        btn.textContent = '\u{1F4CB}';
        btn.title = 'Copy ECharts config';
        btn.setAttribute('aria-label', 'Copy ECharts config');
        btn.style.cssText =
          'position:absolute;top:8px;right:8px;width:28px;height:28px;' +
          'display:flex;align-items:center;justify-content:center;cursor:pointer;' +
          'background:rgba(232,246,249,0.9);border:1px solid rgba(24,102,133,0.20);' +
          'border-radius:4px;font-size:14px;z-index:100;opacity:0;transition:opacity 0.2s;' +
          'padding:0;';
        chartRef.current.onmouseenter = () => { btn.style.opacity = '1'; };
        chartRef.current.onmouseleave = () => { btn.style.opacity = '0'; };
        chartRef.current.onfocus = () => { btn.style.opacity = '1'; };
        chartRef.current.onblur = () => { btn.style.opacity = '0'; };
        btn.onclick = (e: MouseEvent) => {
          e.stopPropagation();
          navigator.clipboard.writeText(JSON.stringify(safeOption, null, 2)).then(() => {
            btn.textContent = '\u2713';
            btn.title = 'Copied';
            setTimeout(() => {
              btn.textContent = '\u{1F4CB}';
              btn.title = 'Copy ECharts config';
            }, 1500);
          });
        };
        chartRef.current.appendChild(btn);
      }
    });

    return () => {
      // L-03: 标记取消，防止异步 import 完成后创建孤儿实例
      cancelled = true;
      // L-03: 断开 ResizeObserver
      if (resizeObserver) {
        resizeObserver.disconnect();
        resizeObserver = null;
      }
      // L-03: dispose ECharts 实例，释放内存
      if (chartInstanceRef.current) {
        chartInstanceRef.current.dispose();
        chartInstanceRef.current = null;
      }
    };
  }, [parsed]);

  if (!parsed) {
    // 流式传输中：JSON 可能还没传完，不显示 error 也不占高度
    const looksIncomplete = debouncedStr.trim().length > 0 && !debouncedStr.trim().endsWith('}');
    if (looksIncomplete) {
      return <div style={{ width: '100%', minHeight: 40, margin: '8px 0' }} />;
    }
    return <Text type="danger">Chart config parse error</Text>;
  }

  return (
    <div
      ref={chartRef}
      style={{ width: '100%', height: 400, margin: '8px 0', position: 'relative' }}
    />
  );
}

/**
 * 内容块化 Markdown 渲染器。
 *
 * 在 react-markdown 的 components 回调中，对可操作块（echarts/plotly 代码块、
 * 表格、h2/h3 标题）用 BlockWrapper 包裹，分配 block_index 并设置 data-block-id。
 *
 * block_index 规则：按块出现顺序从 0 开始递增，同一消息内唯一。
 */
function BlockifiedMarkdown({
  content,
  messageId,
  conversationId,
  systemContext,
}: {
  content: string;
  messageId: string;
  conversationId: string | null;
  systemContext: string | null | undefined;
}): JSX.Element {
  // 块计数器：每次渲染重置，按出现顺序递增
  // 使用 ref 避免触发重渲染，react-markdown 同步渲染保证顺序稳定
  const blockCounterRef = useRef(0);
  blockCounterRef.current = 0;
  const getNextIndex = (): number => blockCounterRef.current++;

  // 预处理：从原始 Markdown 提取每个标题对应的完整 section（标题 + 正文直到下一个同级或更高级标题）
  // 用于 contentSnapshot，使橱窗卡片展开时能看到完整内容而非仅标题
  const headingSections = useMemo(() => {
    const sections: Record<string, string> = {};
    const lines = content.split('\n');
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const h2Match = line.match(/^##\s+(.+)/);
      const h3Match = line.match(/^###\s+(.+)/);
      if (h2Match || h3Match) {
        const headingText = (h2Match || h3Match)![1].trim();
        const level = h2Match ? 2 : 3;
        const sectionLines: string[] = [line];
        i++;
        // 收集直到下一个同级或更高级标题的正文
        while (i < lines.length) {
          const nextLine = lines[i];
          const nextH2 = nextLine.match(/^##\s+/);
          const nextH3 = nextLine.match(/^###\s+/);
          // h2 结束条件：遇到下一个 ## 或 ###
          // h3 结束条件：遇到下一个 ###（## 也终止，因为 ## 是更高级别）
          if ((level === 2 && (nextH2 || nextH3)) || (level === 3 && nextH3)) {
            break;
          }
          sectionLines.push(nextLine);
          i++;
        }
        // 用 headingText 做 key（去重：取第一个匹配的）
        if (!(headingText in sections)) {
          sections[headingText] = sectionLines.join('\n').trim();
        }
      } else {
        i++;
      }
    }
    return sections;
  }, [content]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath, remarkGfm]}
      rehypePlugins={[[rehypeSanitize, sanitizeSchema], rehypeKatex]}
      components={{
        code: ({
          className,
          children,
        }: {
          className?: string;
          children?: ReactNode;
        }) => {
          const lang = className?.replace('language-', '') || '';
          const codeStr = String(children || '').replace(/\n$/, '');

          // ECharts 代码块 → BlockWrapper + ChartBlock
          if (lang === 'echarts') {
            const idx = getNextIndex();
            return (
              <BlockWrapper
                messageId={messageId}
                blockIndex={idx}
                blockType="echarts"
                conversationId={conversationId}
                systemContext={systemContext}
                contentSnapshot={codeStr}
              >
                <ChartBlock optionStr={codeStr} />
              </BlockWrapper>
            );
          }

          // Plotly 代码块 → BlockWrapper + PlotlyBlock
          if (lang === 'plotly') {
            const idx = getNextIndex();
            return (
              <BlockWrapper
                messageId={messageId}
                blockIndex={idx}
                blockType="plotly"
                conversationId={conversationId}
                systemContext={systemContext}
                contentSnapshot={codeStr}
              >
                <PlotlyBlock optionStr={codeStr} />
              </BlockWrapper>
            );
          }

          // 普通代码块
          return (
            <pre
              style={{
                background: 'rgba(142,191,208,0.16)',
                padding: '8px 12px',
                borderRadius: 6,
                overflow: 'auto',
                margin: '6px 0',
                fontSize: 13,
                fontFamily: 'var(--ocean-font-mono, monospace)',
              }}
            >
              <code className={className}>{children}</code>
            </pre>
          );
        },
        table: ({ children }: { children?: ReactNode }) => {
          const idx = getNextIndex();
          const textSnapshot = extractTextFromNode(children);
          return (
            <BlockWrapper
              messageId={messageId}
              blockIndex={idx}
              blockType="table"
              conversationId={conversationId}
              systemContext={systemContext}
              contentSnapshot={textSnapshot}
            >
              <div style={{ overflowX: 'auto', margin: '8px 0' }}>
                <table>{children}</table>
              </div>
            </BlockWrapper>
          );
        },
        h2: ({ children }: { children?: ReactNode }) => {
          const idx = getNextIndex();
          const headingText = extractTextFromNode(children).trim();
          const sectionSnapshot = headingSections[headingText] ?? headingText;
          return (
            <BlockWrapper
              messageId={messageId}
              blockIndex={idx}
              blockType="conclusion"
              conversationId={conversationId}
              systemContext={systemContext}
              contentSnapshot={sectionSnapshot}
            >
              <h2>{children}</h2>
            </BlockWrapper>
          );
        },
        h3: ({ children }: { children?: ReactNode }) => {
          const idx = getNextIndex();
          const headingText = extractTextFromNode(children).trim();
          const sectionSnapshot = headingSections[headingText] ?? headingText;
          return (
            <BlockWrapper
              messageId={messageId}
              blockIndex={idx}
              blockType="conclusion"
              conversationId={conversationId}
              systemContext={systemContext}
              contentSnapshot={sectionSnapshot}
            >
              <h3>{children}</h3>
            </BlockWrapper>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

/**
 * Message role -> avatar letter
 */
const ROLE_AVATAR_TEXT: Record<string, string> = {
  user: 'U',
  assistant: 'AI',
  tool: 'T',
};

/**
 * Message role -> avatar color
 */
const ROLE_COLOR: Record<string, string> = {
  user: '#1686AE',
  assistant: '#14765E',
  tool: '#9A6818',
};

/**
 * Message role -> Chinese label
 */
const ROLE_LABEL: Record<string, string> = {
  user: '\u6211',
  assistant: '\u5c0f\u827e',
  tool: '\u5de5\u5177',
};

/**
 * Message thread component.
 *
 * Displays conversation history, distinguishing user messages,
 * AI responses, and tool messages.
 * AI responses use safe Markdown rendering (react-markdown + rehype-sanitize).
 * AI 消息内容块化渲染：echarts/plotly/table/conclusion 块可加入橱窗。
 *
 * H-14: No dangerouslySetInnerHTML, no regex-based HTML construction.
 */
export function MessageThread({
  messages,
  conversationId,
  systemContext,
}: {
  messages: AssistantMessage[];
  /** 当前对话 ID（传入 BlockWrapper 用于加入橱窗） */
  conversationId?: string | null;
  /** 当前对话的 system_context（用于解析 data_source） */
  systemContext?: string | null;
}): JSX.Element {
  if (messages.length === 0) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          color: 'var(--ocean-text-muted)',
        }}
      >
        <Text type="secondary">{'\u5f00\u59cb\u4e00\u6bb5\u65b0\u5bf9\u8bdd\u5427'}</Text>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {messages.map((msg: AssistantMessage) => {
        const isUser = msg.role === 'user';
        const toolCalls: ToolCallSummary[] = msg.tool_calls ?? [];
        const citations: Citation[] = msg.citations ?? [];

        return (
          <div
            key={msg.id}
            id={`msg-${msg.id}`}
            style={{
              display: 'flex',
              flexDirection: isUser ? 'row-reverse' : 'row',
              gap: 12,
              alignItems: 'flex-start',
            }}
          >
            <Avatar
              size={36}
              style={{
                backgroundColor: ROLE_COLOR[msg.role] ?? '#1686AE',
                flexShrink: 0,
                fontSize: 14,
                fontWeight: 600,
              }}
            >
              {ROLE_AVATAR_TEXT[msg.role] ?? 'AI'}
            </Avatar>
            <div
              style={{
                maxWidth: '75%',
                padding: '12px 16px',
                borderRadius: 12,
                background: isUser ? 'rgba(22, 134, 174, 0.10)' : 'rgba(20, 118, 94, 0.06)',
                border: `1px solid ${isUser ? 'rgba(22, 134, 174, 0.20)' : 'rgba(20, 118, 94, 0.18)'}`,
              }}
            >
              <div style={{ marginBottom: 4 }}>
                <Text
                  type="secondary"
                  style={{ fontSize: 11, fontWeight: 600 }}
                >
                  {ROLE_LABEL[msg.role] ?? msg.role}
                </Text>
              </div>
              {isUser ? (
                <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                  {msg.content}
                </Paragraph>
              ) : (
                <div className="ai-markdown-body">
                  <style>{katexStyle}</style>
                  <BlockifiedMarkdown
                    content={msg.content}
                    messageId={msg.id}
                    conversationId={conversationId ?? null}
                    systemContext={systemContext}
                  />
                </div>
              )}

              {/* Tool call traces (AI messages only) */}
              {!isUser && toolCalls.length > 0 && <ToolTrace toolCalls={toolCalls} />}

              {/* Citation list (AI messages only) */}
              {!isUser && citations.length > 0 && (
                <CitationList citations={citations} />
              )}

              {/* Uncertainty note */}
              {!isUser && msg.uncertainty && (
                <div style={{ marginTop: 8 }}>
                  <Text
                    type="warning"
                    style={{ fontSize: 12 }}
                  >
                    {'\u26a0'} {msg.uncertainty}
                  </Text>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default MessageThread;
