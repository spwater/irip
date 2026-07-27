import { Avatar, Typography } from 'antd';
import CitationList from '@/assistant/CitationList';
import ToolTrace from '@/assistant/ToolTrace';
import type { AssistantMessage, Citation, ToolCallSummary } from '@/api/client';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import remarkGfm from 'remark-gfm';
import katex from 'katex';
import { visit } from 'unist-util-visit';

const { Text, Paragraph } = Typography;

/**
 * 自定义 rehype 插件：处理 math 节点，用 katex.renderToString 生成 HTML
 *
 * 兼容 remark-math v6 生成的节点类型：mathinline / inlineMath / math / displayMath
 */
function rehypeKatexCustom() {
  return (tree: any) => {
    visit(tree, (node: any) => {
      if (
        node.type === 'math' ||
        node.type === 'inlineMath' ||
        node.type === 'mathinline' ||
        node.type === 'displayMath'
      ) {
        const mathStr = node.value || (node.children?.[0]?.value ?? '');
        const displayMode = node.type === 'math' || node.type === 'displayMath';
        try {
          const html = katex.renderToString(mathStr, {
            displayMode,
            throwOnError: false,
            strict: false,
          });
          node.type = 'element';
          node.tagName = displayMode ? 'div' : 'span';
          node.properties = {
            className: displayMode ? 'katex-display' : '',
            dangerouslySetInnerHTML: { __html: html },
          };
          node.children = [];
        } catch {
          // 渲染失败保留原文
        }
      }
    });
  };
}

// KaTeX 公式样式修正：确保上下标正确显示，公式不溢出
const katexStyle = `
.ai-markdown-body .katex { font-size: 1.05em; }
.ai-markdown-body .katex-display { overflow-x: auto; overflow-y: hidden; margin: 8px 0; }
.ai-markdown-body .katex-display::-webkit-scrollbar { height: 4px; }
.ai-markdown-body .katex .vlist-t { border-collapse: collapse; }
.ai-markdown-body .katex .vlist-r { display: table-row; }
.ai-markdown-body .katex .vlist { display: table-cell; position: relative; vertical-align: bottom; }
.ai-markdown-body .katex .vlist > span { display: block; height: 0; position: relative; }
.ai-markdown-body .katex .vlist > span > span { display: inline-block; }
.ai-markdown-body .katex .msupsub { text-align: left; }
.ai-markdown-body .katex .mfrac > span > span { text-align: center; }
.ai-markdown-body .katex .mfrac .frac-line { border-bottom-style: solid; display: inline-block; width: 100%; }
`;

/**
 * 消息角色 → 头像首字母
 */
const ROLE_AVATAR_TEXT: Record<string, string> = {
  user: 'U',
  assistant: 'AI',
  tool: 'T',
};

/**
 * 消息角色 → 头像颜色
 */
const ROLE_COLOR: Record<string, string> = {
  user: '#1677ff',
  assistant: '#52c41a',
  tool: '#fa8c16',
};

/**
 * 消息角色 → 中文名
 */
const ROLE_LABEL: Record<string, string> = {
  user: '我',
  assistant: 'AI 助手',
  tool: '工具',
};

/**
 * Markdown 组件样式覆盖
 *
 * 让 AI 回答中的标题、表格、代码块、公式等正确渲染。
 */
const markdownComponents = {
  h1: ({ node, ...props }: any) => <h1 style={{ fontSize: 18, fontWeight: 700, margin: '12px 0 8px' }} {...props} />,
  h2: ({ node, ...props }: any) => <h2 style={{ fontSize: 16, fontWeight: 700, margin: '10px 0 6px' }} {...props} />,
  h3: ({ node, ...props }: any) => <h3 style={{ fontSize: 15, fontWeight: 600, margin: '8px 0 4px' }} {...props} />,
  h4: ({ node, ...props }: any) => <h4 style={{ fontSize: 14, fontWeight: 600, margin: '6px 0 4px' }} {...props} />,
  p: ({ node, ...props }: any) => <p style={{ margin: '4px 0', lineHeight: 1.7 }} {...props} />,
  ul: ({ node, ...props }: any) => <ul style={{ margin: '4px 0', paddingLeft: 20 }} {...props} />,
  ol: ({ node, ...props }: any) => <ol style={{ margin: '4px 0', paddingLeft: 20 }} {...props} />,
  li: ({ node, ...props }: any) => <li style={{ margin: '2px 0', lineHeight: 1.7 }} {...props} />,
  table: ({ node, ...props }: any) => (
    <table
      style={{
        borderCollapse: 'collapse',
        width: '100%',
        margin: '8px 0',
        fontSize: 13,
      }}
      {...props}
    />
  ),
  th: ({ node, ...props }: any) => (
    <th
      style={{
        border: '1px solid #d9d9d9',
        padding: '6px 10px',
        background: '#fafafa',
        fontWeight: 600,
        textAlign: 'left',
      }}
      {...props}
    />
  ),
  td: ({ node, ...props }: any) => (
    <td
      style={{
        border: '1px solid #d9d9d9',
        padding: '6px 10px',
      }}
      {...props}
    />
  ),
  code: ({ node, className, children, ...props }: any) => {
    // react-markdown v10: 通过 className 判断行内代码 vs 代码块
    // 代码块有 className="language-xxx"，行内代码没有
    const isBlock = className && typeof className === 'string' && className.includes('language-');
    if (!isBlock) {
      return (
        <code
          style={{
            background: '#f0f0f0',
            padding: '1px 4px',
            borderRadius: 3,
            fontSize: 13,
            fontFamily: 'monospace',
          }}
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <code
        className={className}
        style={{
          display: 'block',
          background: '#f5f5f5',
          padding: '8px 12px',
          borderRadius: 6,
          fontSize: 13,
          fontFamily: 'monospace',
          overflow: 'auto',
          margin: '6px 0',
        }}
        {...props}
      >
        {children}
      </code>
    );
  },
  blockquote: ({ node, ...props }: any) => (
    <blockquote
      style={{
        borderLeft: '3px solid #91caff',
        margin: '6px 0',
        padding: '4px 12px',
        color: '#666',
        background: '#f6f8fa',
      }}
      {...props}
    />
  ),
  a: ({ node, ...props }: any) => (
    <a style={{ color: '#1677ff' }} target="_blank" rel="noopener noreferrer" {...props} />
  ),
};

/**
 * 消息列表组件
 *
 * 展示对话历史，区分用户消息、AI 回答与工具消息。
 * AI 回答使用 Markdown 渲染（含表格、公式、代码块等）。
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
          color: '#999',
        }}
      >
        <Text type="secondary">开始一段新对话吧 ✨</Text>
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
                backgroundColor: ROLE_COLOR[msg.role] ?? '#1677ff',
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
                background: isUser ? '#e6f4ff' : '#f6ffed',
                border: `1px solid ${isUser ? '#91caff' : '#b7eb8f'}`,
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
                  <ReactMarkdown
                    remarkPlugins={[remarkMath, remarkGfm]}
                    rehypePlugins={[rehypeKatexCustom]}
                    components={markdownComponents}
                  >
                    {msg.content}
                  </ReactMarkdown>
                </div>
              )}

              {/* 工具调用轨迹（仅 AI 消息） */}
              {!isUser && toolCalls.length > 0 && <ToolTrace toolCalls={toolCalls} />}

              {/* 引用列表（仅 AI 消息） */}
              {!isUser && citations.length > 0 && (
                <CitationList citations={citations} />
              )}

              {/* 不确定性说明 */}
              {!isUser && msg.uncertainty && (
                <div style={{ marginTop: 8 }}>
                  <Text
                    type="warning"
                    style={{ fontSize: 12 }}
                  >
                    ⚠ {msg.uncertainty}
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
