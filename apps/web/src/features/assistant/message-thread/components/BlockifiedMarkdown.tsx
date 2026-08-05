/**
 * BlockifiedMarkdown — 内容块化 Markdown 渲染器。
 *
 * 从 MessageThread.tsx 提取。
 * 不使用 rehype-katex，自己用 KaTeX JS API 渲染公式。
 * 支持代码块（echarts/plotly）拦截 + 表格/标题加入橱窗。
 */

import { useMemo, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BlockWrapper } from '@/features/assistant/BlockWrapper';
import { PlotlyBlock } from '@/features/assistant/PlotlyBlock';
import { ChartRefBlock } from '@/features/assistant/ChartRefBlock';
import { normalizeLatexMath, preprocessMath } from '../utils/mathUtils';
import { extractTextFromNode, rebuildTableMarkdown } from '../utils/nodeUtils';
import { ChartBlock } from './ChartBlock';
import type { BlockifiedMarkdownProps } from '../types';

// KaTeX 样式隔离已在 global.css 中处理（仅 .katex { line-height: 1.2 }）
// 不在此处添加任何 KaTeX 覆盖样式，避免与 KaTeX 自身 CSS 冲突

export function BlockifiedMarkdown({
  content,
  messageId,
  conversationId,
  systemContext,
}: BlockifiedMarkdownProps): JSX.Element {
  // 预处理：先转换 \[...\] → $$...$$，再把公式替换成占位符
  const { html: preprocessed, mathMap, formulaMap } = useMemo(() => {
    const normalized = normalizeLatexMath(content);
    return preprocessMath(normalized);
  }, [content]);

  // 块计数器：每次 content 变化时通过 useMemo 重建重置（见 components useMemo 内部）
  // 表格独立计数器同理

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
    // 每次组件重新创建时重置计数器（依赖 content 变化触发重建）
    let blockCounter = 0;
    let tableCounter = 0;
    const getNextIndex = (): number => blockCounter++;
    const getNextTableIndex = (): number => tableCounter++;
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
                <BlockWrapper
                  messageId={messageId}
                  blockIndex={idx}
                  blockType="formula"
                  conversationId={conversationId}
                  systemContext={systemContext}
                  contentSnapshot={formulaTex}
                >
                  <div
                    style={{ margin: '8px 0', overflowX: 'auto' }}
                    dangerouslySetInnerHTML={{ __html: mathHtml }}
                  />
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
            return (
              <p>
                {children.map(c =>
                  typeof c === 'string' && c.includes('MATH' as string)
                    ? replacePlaceholders(c as string)
                    : c,
                )}
              </p>
            );
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

        // chart-ref 代码块：从已加载的实验数据引用画图（轻量指令，不含数据点）
        if (lang === 'chart-ref') {
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
              <ChartRefBlock specStr={codeStr} systemContext={systemContext} />
            </BlockWrapper>
          );
        }
        // ECharts 代码块
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
        // Plotly 代码块
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
          <code
            className={className}
            style={{
              background: 'rgba(142,191,208,0.16)',
              padding: '2px 4px',
              borderRadius: 4,
              fontSize: 13,
              fontFamily: 'var(--ocean-font-mono, monospace)',
            }}
          >
            {children}
          </code>
        );
      },
      table: ({ children }: { children?: ReactNode }) => {
        const idx = getNextIndex();
        // 用独立计数器索引 tableSnapshots（因为 blockCounter 对所有块类型统一递增）
        const tableIdx = getNextTableIndex();
        let tableSnapshot = tableSnapshots[tableIdx];
        if (!tableSnapshot) {
          // 回退：从 React table 子节点重建 Markdown 表格文本
          tableSnapshot = rebuildTableMarkdown(children);
        }
        return (
          <BlockWrapper
            messageId={messageId}
            blockIndex={idx}
            blockType="table"
            conversationId={conversationId}
            systemContext={systemContext}
            contentSnapshot={tableSnapshot}
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
    };
  }, [mathMap, formulaMap, messageId, conversationId, systemContext, headingSections, tableSnapshots]);

  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {preprocessed}
    </ReactMarkdown>
  );
}

export default BlockifiedMarkdown;
