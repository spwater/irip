import { Avatar, Typography } from 'antd';
import CitationList from '@/assistant/CitationList';
import ToolTrace from '@/assistant/ToolTrace';
import type { AssistantMessage, Citation, ToolCallSummary } from '@/api/models-ai';
import 'katex/dist/katex.min.css';
import { useEffect, useMemo, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

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
 * ECharts chart block component.
 *
 * H-14: ECharts data is parsed independently, not through Markdown rendering.
 * The option JSON is parsed safely with JSON.parse and rendered via echarts.
 */
function ChartBlock({ optionStr }: { optionStr: string }): JSX.Element {
  const chartRef = useRef<HTMLDivElement>(null);

  const parsed = useMemo(() => {
    try {
      return JSON.parse(optionStr);
    } catch {
      return null;
    }
  }, [optionStr]);

  useEffect(() => {
    if (!parsed || !chartRef.current) return;

    let chart: { setOption: (opt: Record<string, unknown>) => void; dispose: () => void } | null = null;
    let width = 500;

    // Find parent container width
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
      if (!chartRef.current) return;
      chart = echarts.init(chartRef.current, undefined, { width, height: 400 });
      chart.setOption(safeOption);

      // Add copy button
      if (!chartRef.current.querySelector('.echarts-copy-btn')) {
        const btn = document.createElement('div');
        btn.className = 'echarts-copy-btn';
        btn.textContent = '\u{1F4CB}';
        btn.title = 'Copy ECharts config';
        btn.style.cssText =
          'position:absolute;top:8px;right:8px;width:28px;height:28px;' +
          'display:flex;align-items:center;justify-content:center;cursor:pointer;' +
          'background:rgba(232,246,249,0.9);border:1px solid rgba(24,102,133,0.20);' +
          'border-radius:4px;font-size:14px;z-index:100;opacity:0;transition:opacity 0.2s';
        chartRef.current.onmouseenter = () => { btn.style.opacity = '1'; };
        chartRef.current.onmouseleave = () => { btn.style.opacity = '0'; };
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
      if (chart) chart.dispose();
    };
  }, [parsed]);

  if (!parsed) {
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
 * Code block renderer for react-markdown.
 *
 * H-14: Intercepts `echarts` code blocks and renders them as ChartBlock.
 * All other code blocks are rendered normally by react-markdown.
 */
function CodeBlockRenderer({
  className,
  children,
}: {
  className?: string;
  children?: React.ReactNode;
}): JSX.Element {
  const lang = className?.replace('language-', '') || '';
  const codeStr = String(children || '').replace(/\n$/, '');

  if (lang === 'echarts') {
    return <ChartBlock optionStr={codeStr} />;
  }

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
}

/**
 * Render Markdown content safely using react-markdown + rehype-sanitize.
 *
 * H-14: Replaces the previous regex-based HTML rendering with:
 * - react-markdown: safe Markdown parsing (no raw HTML by default)
 * - rehype-sanitize: strict allowlist, no event attributes
 * - remark-math + rehype-katex: KaTeX math rendering
 * - remark-gfm: GitHub-flavored Markdown (tables, etc.)
 * - ECharts data parsed independently, not through Markdown
 *
 * No dangerouslySetInnerHTML is used.
 */
function MarkdownWithMath({ content }: { content: string }): JSX.Element {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath, remarkGfm]}
      rehypePlugins={[[rehypeSanitize, sanitizeSchema], rehypeKatex]}
      components={{
        code: CodeBlockRenderer as never,
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
 *
 * H-14: No dangerouslySetInnerHTML, no regex-based HTML construction.
 */
export function MessageThread({
  messages,
}: {
  messages: AssistantMessage[];
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
                  <MarkdownWithMath content={msg.content} />
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
