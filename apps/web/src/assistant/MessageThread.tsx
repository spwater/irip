import { Avatar, Typography } from 'antd';
import CitationList from '@/assistant/CitationList';
import ToolTrace from '@/assistant/ToolTrace';
import type { AssistantMessage, Citation, ToolCallSummary } from '@/api/client';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { useEffect, useRef } from 'react';

const { Text, Paragraph } = Typography;

// KaTeX 公式样式
const katexStyle = `
.ai-markdown-body .katex { font-size: 1.05em; }
.ai-markdown-body .katex-display { overflow-x: auto; overflow-y: hidden; margin: 8px 0; }
.ai-markdown-body .katex-display::-webkit-scrollbar { height: 4px; }
.ai-markdown-body .katex * { box-sizing: content-box !important; }
`;

/**
 * 渲染包含 LaTeX 公式的 Markdown
 *
 * 方案：
 * 1. 用正则把 $$...$$ 和 $...$ 替换为 <span class="katex-math" data-latex="..." data-display="..."></span>
 * 2. 用简易 Markdown → HTML 转换处理其余语法
 * 3. useEffect 里找到所有 .katex-math span，用 katex.render 渲染公式
 *
 * 这样 KaTeX HTML 完全由 katex.render 生成，不经过 react-markdown 处理。
 */
function MarkdownWithMath({ content }: { content: string }): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);

  // 提取公式，替换为 span 占位符
  const processed = content
    // 块级公式 $$...$$
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, latex: string) => {
      const escaped = latex.trim().replace(/"/g, '&quot;');
      return `<span class="katex-math" data-latex="${escaped}" data-display="true"></span>`;
    })
    // 行内公式 $...$
    .replace(/(?<!\$)\$(?!\$)(.+?)(?<!\$)\$/g, (_, latex: string) => {
      const escaped = latex.trim().replace(/"/g, '&quot;');
      return `<span class="katex-math" data-latex="${escaped}" data-display="false"></span>`;
    });

  // 渲染完后，用 katex.render 替换占位符
  useEffect(() => {
    if (!containerRef.current) return;
    const spans = containerRef.current.querySelectorAll('.katex-math');
    spans.forEach((span) => {
      const latex = span.getAttribute('data-latex') || '';
      const display = span.getAttribute('data-display') === 'true';
      try {
        katex.render(latex, span as HTMLElement, {
          displayMode: display,
          throwOnError: false,
          strict: false,
        });
      } catch {
        span.textContent = latex;
      }
    });
  });

  return (
    <div ref={containerRef} dangerouslySetInnerHTML={{ __html: renderMarkdownToHtml(processed) }} />
  );
}

/**
 * 简易 Markdown → HTML 转换
 *
 * 不用 react-markdown，直接用正则处理常见的 Markdown 语法。
 * 避免 react-markdown 对 HTML 标签的转义和重新处理。
 */
function renderMarkdownToHtml(md: string): string {
  let html = md;

  // 代码块 ```
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, _lang, code) => {
    return `<pre style="background:#f5f5f5;padding:8px 12px;border-radius:6px;overflow:auto;margin:6px 0;font-size:13px;font-family:monospace"><code>${escapeHtml(code.trim())}</code></pre>`;
  });

  // 行内代码 `...`
  html = html.replace(/`([^`]+)`/g, (_, code) => {
    return `<code style="background:#f0f0f0;padding:1px 4px;border-radius:3px;font-size:13px;font-family:monospace">${escapeHtml(code)}</code>`;
  });

  // 标题
  html = html.replace(/^### (.+)$/gm, '<h3 style="font-size:15px;font-weight:600;margin:8px 0 4px">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 style="font-size:16px;font-weight:700;margin:10px 0 6px">$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1 style="font-size:18px;font-weight:700;margin:12px 0 8px">$1</h1>');

  // 粗体 **...**
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // 斜体 *...*
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  // 表格
  html = html.replace(/^\|(.+)\|\n\|([-| :]+)\|\n((?:\|.*\|\n?)*)/gm, (_, header, _sep, body) => {
    const headers = header.split('|').map((h: string) => h.trim()).filter(Boolean);
    const rows = body.trim().split('\n').map((r: string) => r.split('|').map((c: string) => c.trim()).filter(Boolean));
    let table = '<table style="border-collapse:collapse;width:100%;margin:8px 0;font-size:13px">';
    table += '<tr>' + headers.map((h: string) => `<th style="border:1px solid #d9d9d9;padding:6px 10px;background:#fafafa;font-weight:600;text-align:left">${h}</th>`).join('') + '</tr>';
    rows.forEach((row: string[]) => {
      table += '<tr>' + row.map((c: string) => `<td style="border:1px solid #d9d9d9;padding:6px 10px">${c}</td>`).join('') + '</tr>';
    });
    table += '</table>';
    return table;
  });

  // 引用块 >
  html = html.replace(/^> (.+)$/gm, '<blockquote style="border-left:3px solid #91caff;margin:6px 0;padding:4px 12px;color:#666;background:#f6f8fa">$1</blockquote>');

  // 无序列表 - 或 *
  html = html.replace(/^[-*] (.+)$/gm, '<li style="margin:2px 0;line-height:1.7;padding-left:4px">$1</li>');
  html = html.replace(/(<li[^<]*<\/li>\n?)+/g, (m) => `<ul style="margin:4px 0;padding-left:20px">${m}</ul>`);

  // 有序列表 1.
  html = html.replace(/^\d+\. (.+)$/gm, '<li style="margin:2px 0;line-height:1.7;padding-left:4px">$1</li>');

  // 分隔线 ---
  html = html.replace(/^---$/gm, '<hr style="border:none;border-top:1px solid #e8e8e8;margin:12px 0" />');

  // 段落（把连续的非标签行用 p 包裹）
  html = html.replace(/^(?!<[a-z/])((?!<[a-z]).+)$/gm, '<p style="margin:4px 0;line-height:1.7">$1</p>');

  // 清理多余空行
  html = html.replace(/\n{3,}/g, '\n\n');

  return html;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

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
                  <MarkdownWithMath content={msg.content} />
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
