import { Avatar, Typography } from 'antd';
import CitationList from '@/features/assistant/CitationList';
import ToolTrace from '@/features/assistant/ToolTrace';
import type { AssistantMessage, Citation, ToolCallSummary } from '@/api/models-ai';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { BlockWrapper } from '@/features/assistant/BlockWrapper';
import { PlotlyBlock } from '@/features/assistant/PlotlyBlock';

const { Text, Paragraph } = Typography;

// KaTeX 样式隔离已在 global.css 中处理（仅 .katex { line-height: 1.2 }）
// 不在此处添加任何 KaTeX 覆盖样式，避免与 KaTeX 自身 CSS 冲突
const katexStyle = '';

/**
 * Custom sanitize schema for rehype-sanitize.
 *
 * Extends the default schema to allow all KaTeX-generated elements and attributes.
 * KaTeX generates deeply nested span/div structures with class names, style attributes,
 * aria-* attributes, and MathML elements (semantics, annotation, math, mrow, mi, mo, etc.)
 */
/* sanitizeSchema 已移除（不再使用 rehype-sanitize） */

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
 * 将各种数学公式语法统一转换为 remark-math 能识别的 $$...$$ 和 $...$ 语法。
 *
 * 支持的输入格式：
 * - \[...\]  LaTeX display math（跨行或单行）
 * - [...]     纯方括号 display math（AI 常见输出，行首行尾独占）
 * - \(...\)   LaTeX inline math
 * - (...)     纯括号 inline math（需包含 LaTeX 命令才转换，避免误匹配普通括号）
 *
 * 注意：纯方括号 [ ... ] 仅在包含 LaTeX 命令（\frac, \sum, \bar, \hat, \sqrt 等）时才转换，
 * 避免误匹配引用标注 [1] 或普通文本。
 */
/**
 * 将 LaTeX 原始语法 \[...\] 和 [...] 转换为 $$...$$ 语法。
 */
function normalizeLatexMath(md: string): string {
  const hasLatexCmd = (s: string): boolean => /\\(frac|sum|bar|hat|sqrt|int|oint|partial|nabla|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|sigma|omega|pi|infty|cdot|times|div|pm|mp|le|ge|ne|approx|equiv|propto|leq|geq|subset|supset|in|notin|cup|cap|forall|exists|mathbb|mathcal|mathbf|text|mathrm|left|right|begin|end|operatorname)/.test(s);

  let result = md;
  // \[...\] 跨行 → $$...$$
  result = result.replace(/(^|\n)\s*\\\[\s*\n([\s\S]*?)\n\s*\\\]\s*(?=\n|$)/g, '$1\n$$ $2 $$\n');
  // \[...\] 单行 → $$...$$
  result = result.replace(/(^|\n)\s*\\\[([\s\S]*?)\\\]\s*(?=\n|$)/g, '$1$$ $2 $$');
  // \(...\) → $...$
  result = result.replace(/\\\(([\s\S]*?)\\\)/g, (match, inner) => {
    if (hasLatexCmd(inner)) return `$${inner}$`;
    return match;
  });
  // 纯方括号 [ ... ] 独占行 → $$...$$（仅含 LaTeX 命令时）
  result = result.replace(/(^|\n)\s*\[\s*\n([\s\S]*?)\n\s*\]\s*(?=\n|$)/g, (match, prefix, inner) => {
    if (hasLatexCmd(inner)) return `${prefix}\n$$ ${inner.trim()} $$\n`;
    return match;
  });
  result = result.replace(/(^|\n)\s*\[([\s\S]*?)\]\s*(?=\n|$)/g, (match, prefix, inner) => {
    if (hasLatexCmd(inner)) return `${prefix}$$ ${inner.trim()} $$`;
    return match;
  });
  return result;
}

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
 * 用 KaTeX JS API 直接渲染公式为 HTML 字符串。
 * 绕开 rehype-katex 的 hast 节点转换（在 react-markdown v10 中有兼容性问题）。
 */
function renderMath(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex, {
      displayMode,
      throwOnError: false,
      strict: false,
    });
  } catch {
    return `<span style="color:red">${tex}</span>`;
  }
}

/**
 * 预处理：把 $$...$$ 和 $...$ 公式用 KaTeX 渲染成 HTML，
 * 然后用特殊占位符标记，后续在 ReactMarkdown 渲染后替换。
 *
 * 不用 rehype-katex，直接用 KaTeX JS API 生成 HTML 字符串。
 */
function preprocessMath(md: string): { html: string; mathMap: Map<string, string>; formulaMap: Map<string, string> } {
  const mathMap = new Map<string, string>();
  const formulaMap = new Map<string, string>();
  let counter = 0;
  let result = md;

  // 先处理 $$...$$ display math（非贪婪，跨行允许）
  result = result.replace(/\$\$([\s\S]*?)\$\$/g, (_, tex: string) => {
    const html = renderMath(tex.trim(), true);
    const placeholder = `MATHDISPLAY${counter}MATHEND`;
    mathMap.set(placeholder, html);
    formulaMap.set(placeholder, `$$${tex.trim()}$$`);
    counter++;
    return placeholder;
  });

  // 再处理 $...$ inline math（不跨行，避免误匹配）
  result = result.replace(/\$([^\n$]+?)\$/g, (_, tex: string) => {
    const html = renderMath(tex.trim(), false);
    const placeholder = `MATHINLINE${counter}MATHEND`;
    mathMap.set(placeholder, html);
    counter++;
    return placeholder;
  });

  return { html: result, mathMap, formulaMap };
}

/**
 * 内容块化 Markdown 渲染器。
 * 不使用 rehype-katex，自己用 KaTeX JS API 渲染公式。
 * 支持代码块（echarts/plotly）拦截 + 表格/标题加入橱窗。
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
  // 预处理：先转换 \[...\] → $$...$$，再把公式替换成占位符
  const { html: preprocessed, mathMap, formulaMap } = useMemo(() => {
    const normalized = normalizeLatexMath(content);
    return preprocessMath(normalized);
  }, [content]);

  // 块计数器
  const blockCounterRef = useRef(0);
  blockCounterRef.current = 0;
  const getNextIndex = (): number => blockCounterRef.current++;
  // 表格独立计数器（blockCounter 对所有块类型统一递增，tableSnapshots 只含表格）
  const tableCounterRef = useRef(0);
  tableCounterRef.current = 0;

  // 预提取标题 sections 和表格原文（在 preprocessMath 之前，用 normalizeLatexMath 的输出）
  const normalizedContent = useMemo(() => normalizeLatexMath(content), [content]);
  const { headingSections, tableSnapshots } = useMemo(() => {
    const sections: Record<string, string> = {};
    const tables: string[] = [];
    const lines = normalizedContent.split('\n');
    let i = 0;
    // 标题 sections
    i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const h2Match = line.match(/^##\s+(.+)/);
      const h3Match = line.match(/^###\s+(.+)/);
      if (h2Match || h3Match) {
        const headingText = (h2Match || h3Match)![1].trim();
        const level = h2Match ? 2 : 3;
        const sectionLines: string[] = [line];
        i++;
        while (i < lines.length) {
          const nextLine = lines[i];
          const nextH2 = nextLine.match(/^##\s+/);
          const nextH3 = nextLine.match(/^###\s+/);
          if ((level === 2 && (nextH2 || nextH3)) || (level === 3 && nextH3)) break;
          sectionLines.push(nextLine);
          i++;
        }
        if (!(headingText in sections)) {
          sections[headingText] = sectionLines.join('\n').trim();
        }
      } else {
        i++;
      }
    }
    // 表格原文
    i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (line.includes('|') && !line.match(/^#{1,3}\s/)) {
        const tableLines: string[] = [];
        let j = i;
        while (j < lines.length && lines[j].includes('|') && !lines[j].match(/^#{1,3}\s/)) {
          tableLines.push(lines[j]);
          j++;
        }
        if (tableLines.length >= 2 && tableLines[1].includes('---')) {
          tables.push(tableLines.join('\n'));
        }
        i = j;
      } else {
        i++;
      }
    }
    return { headingSections: sections, tableSnapshots: tables };
  }, [preprocessed]);

  // 自定义组件
  const components = useMemo(() => {
    const replacePlaceholders = (text: string): ReactNode => {
      if (!text.includes('MATH')) return text;
      const parts: ReactNode[] = [];
      const regex = /(MATH(?:DISPLAY|INLINE)\d+MATHEND)/g;
      let lastIndex = 0;
      let match: RegExpExecArray | null;
      let key = 0;
      while ((match = regex.exec(text)) !== null) {
        if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
        const placeholder = match[1];
        const mathHtml = mathMap.get(placeholder);
        if (mathHtml) {
          parts.push(<span key={`math-${key}`} dangerouslySetInnerHTML={{ __html: mathHtml }} />);
        } else {
          parts.push(placeholder);
        }
        lastIndex = match.index + placeholder.length;
        key++;
      }
      if (lastIndex < text.length) parts.push(text.slice(lastIndex));
      return parts.length === 1 ? parts[0] : <>{parts}</>;
    };

    return {
      p: ({ children }: { children?: ReactNode }) => {
        if (typeof children === 'string') {
          // 检测整段是否是单个 display 公式占位符 → 用 BlockWrapper 包裹支持加入橱窗
          const displayMatch = children.match(/^(MATHDISPLAY\d+MATHEND)$/);
          if (displayMatch) {
            const placeholder = displayMatch[1];
            const mathHtml = mathMap.get(placeholder);
            const formulaTex = formulaMap.get(placeholder);
            if (mathHtml && formulaTex) {
              const idx = getNextIndex();
              return (
                <BlockWrapper messageId={messageId} blockIndex={idx} blockType="formula"
                  conversationId={conversationId} systemContext={systemContext} contentSnapshot={formulaTex}>
                  <div style={{ margin: '8px 0', overflowX: 'auto' }} dangerouslySetInnerHTML={{ __html: mathHtml }} />
                </BlockWrapper>
              );
            }
          }
          if (children.includes('MATH')) {
            return <p>{replacePlaceholders(children)}</p>;
          }
        }
        if (Array.isArray(children)) {
          const hasMath = children.some(c => typeof c === 'string' && c.includes('MATH'));
          if (hasMath) {
            return <p>{children.map(c => typeof c === 'string' && c.includes('MATH') ? replacePlaceholders(c) : c)}</p>;
          }
        }
        return <p>{children}</p>;
      },
      code: ({
        className,
        children,
      }: {
        className?: string;
        children?: ReactNode;
      }) => {
        const text = String(children || '');
        // 公式占位符优先处理
        if (text.includes('MATH')) {
          return <>{replacePlaceholders(text)}</>;
        }
        const lang = className?.replace('language-', '') || '';
        const codeStr = text.replace(/\n$/, '');

        // ECharts 代码块
        if (lang === 'echarts') {
          const idx = getNextIndex();
          return (
            <BlockWrapper messageId={messageId} blockIndex={idx} blockType="echarts"
              conversationId={conversationId} systemContext={systemContext} contentSnapshot={codeStr}>
              <ChartBlock optionStr={codeStr} />
            </BlockWrapper>
          );
        }
        // Plotly 代码块
        if (lang === 'plotly') {
          const idx = getNextIndex();
          return (
            <BlockWrapper messageId={messageId} blockIndex={idx} blockType="plotly"
              conversationId={conversationId} systemContext={systemContext} contentSnapshot={codeStr}>
              <PlotlyBlock optionStr={codeStr} />
            </BlockWrapper>
          );
        }
        // 普通代码块
        return <code className={className} style={{
          background: 'rgba(142,191,208,0.16)', padding: '2px 4px', borderRadius: 4,
          fontSize: 13, fontFamily: 'var(--ocean-font-mono, monospace)',
        }}>{children}</code>;
      },
      table: ({ children }: { children?: ReactNode }) => {
        const idx = getNextIndex();
        // 用独立计数器索引 tableSnapshots（因为 blockCounter 对所有块类型统一递增）
        const tableIdx = tableCounterRef.current++;
        const tableSnapshot = tableSnapshots[tableIdx] ?? extractTextFromNode(children);
        return (
          <BlockWrapper messageId={messageId} blockIndex={idx} blockType="table"
            conversationId={conversationId} systemContext={systemContext} contentSnapshot={tableSnapshot}>
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
          <BlockWrapper messageId={messageId} blockIndex={idx} blockType="conclusion"
            conversationId={conversationId} systemContext={systemContext} contentSnapshot={sectionSnapshot}>
            <h2>{children}</h2>
          </BlockWrapper>
        );
      },
      h3: ({ children }: { children?: ReactNode }) => {
        const idx = getNextIndex();
        const headingText = extractTextFromNode(children).trim();
        const sectionSnapshot = headingSections[headingText] ?? headingText;
        return (
          <BlockWrapper messageId={messageId} blockIndex={idx} blockType="conclusion"
            conversationId={conversationId} systemContext={systemContext} contentSnapshot={sectionSnapshot}>
            <h3>{children}</h3>
          </BlockWrapper>
        );
      },
    };
  }, [mathMap, formulaMap, messageId, conversationId, systemContext, headingSections, tableSnapshots]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={components}
    >
      {preprocessed}
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
                maxWidth: 'calc(100% - 48px)',
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
