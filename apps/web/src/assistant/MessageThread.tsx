import { Avatar, Typography } from 'antd';
import CitationList from '@/assistant/CitationList';
import ToolTrace from '@/assistant/ToolTrace';
import { createOceanChartOptions } from '@/theme/chartTheme';
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
 *
 * Data Ocean Phase 4：用 createOceanChartOptions 合并图表主题，
 * 用 data-echarts / data-echarts-animation 属性暴露动效配置，
 * 用语义 CSS class 替换硬编码白色/灰色。保留 Markdown 正则、KaTeX、复制行为不变。
 */
function MarkdownWithMath({ content }: { content: string }): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartOptions = useRef<string[]>([]);

  // 提取公式，替换为占位符
  let processed = content
    // 块级公式 $$...$$ → 用 div 占位（display 模式需要块级元素）
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, latex: string) => {
      const escaped = latex.trim().replace(/"/g, '&quot;');
      return `<div class="katex-math" data-latex="${escaped}" data-display="true"></div>`;
    })
    // 行内公式 $...$ → 用 span 占位
    .replace(/(?<!\$)\$(?!\$)(.+?)(?<!\$)\$/g, (_, latex: string) => {
      const escaped = latex.trim().replace(/"/g, '&quot;');
      return `<span class="katex-math" data-latex="${escaped}" data-display="false"></span>`;
    });

  // 提取 echarts 代码块，存到 ref 数组，div 只存索引
  chartOptions.current = [];
  const reducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  processed = processed.replace(/```echarts\n([\s\S]*?)```/g, (_, code) => {
    const idx = chartOptions.current.length;
    chartOptions.current.push(code.trim());
    return `<div class="echarts-chart ocean-chart-container" data-idx="${idx}" data-echarts="true" data-echarts-animation="${!reducedMotion}" style="width:100%;height:400px;margin:8px 0"></div>`;
  });

  // 渲染完后，用 katex.render 替换占位符
  useEffect(() => {
    if (!containerRef.current) return;
    // 渲染 KaTeX 公式
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
    // 渲染 ECharts 图表
    const charts = containerRef.current.querySelectorAll('.echarts-chart');
    charts.forEach((div) => {
      const idx = parseInt(div.getAttribute('data-idx') || '-1', 10);
      if (idx < 0 || idx >= chartOptions.current.length) return;
      const optionStr = chartOptions.current[idx];
      try {
        const option = JSON.parse(optionStr);
        // 只补充 containLabel 防裁切，不覆盖 LLM 的 grid 值
        if (!option.grid) option.grid = {};
        option.grid.containLabel = true;
        // X 轴名称放到正下方
        if (option.xAxis && !Array.isArray(option.xAxis)) {
          option.xAxis.nameLocation = 'middle';
          option.xAxis.nameGap = 25;
        }
        // 用 Data Ocean 主题合并视觉默认值（不覆盖 series / grid / yAxis）
        const themedOption = createOceanChartOptions(option, reducedMotion);
        // 动态导入 echarts 避免首屏加载慢
        import('echarts').then((echarts) => {
          // 找消息气泡容器（向上遍历到有 padding 的 div）
          let el: HTMLElement = div as HTMLElement;
          let width = 0;
          while (el.parentElement) {
            el = el.parentElement;
            if (el.clientWidth > 0) {
              width = el.clientWidth - 32; // 减去 padding (12px*2 + 一点余量)
              break;
            }
          }
          if (width < 200) width = 500; // 兜底
          (div as HTMLElement).style.width = '100%';
          (div as HTMLElement).style.position = 'relative';
          const chart = echarts.init(div as HTMLElement, undefined, { width, height: 400 });
          chart.setOption(themedOption);

          // 在图表右上角添加复制按钮（hover 时显示）
          if (!div.querySelector('.echarts-copy-btn')) {
            const btn = document.createElement('div');
            btn.className = 'echarts-copy-btn';
            btn.innerHTML = '📋';
            btn.title = '复制 ECharts 配置';
            btn.style.cssText = 'position:absolute;top:8px;right:8px;width:28px;height:28px;display:flex;align-items:center;justify-content:center;cursor:pointer;background:rgba(232, 246, 249, 0.90);border:1px solid rgba(24, 102, 133, 0.16);border-radius:4px;font-size:14px;z-index:100;opacity:0;transition:opacity 0.2s';
            (div as HTMLElement).onmouseenter = () => { btn.style.opacity = '1'; };
            (div as HTMLElement).onmouseleave = () => { btn.style.opacity = '0'; };
            btn.onclick = (e) => {
              e.stopPropagation();
              navigator.clipboard.writeText(JSON.stringify(option, null, 2)).then(() => {
                btn.innerHTML = '✓';
                btn.title = '已复制';
                setTimeout(() => { btn.innerHTML = '📋'; btn.title = '复制 ECharts 配置'; }, 1500);
              });
            };
            (div as HTMLElement).appendChild(btn);
          }
        });
      } catch (e) {
        div.textContent = '图表配置解析失败: ' + (e as Error).message;
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
 *
 * Data Ocean Phase 4：用语义 CSS class 替换硬编码颜色，
 * 保留正则逻辑和转义行为不变。
 */
function renderMarkdownToHtml(md: string): string {
  let html = md;

  // 代码块 ```（echarts 已在前面提取，这里只处理普通代码块）
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, _lang, code) => {
    return `<pre class="ocean-md-pre"><code>${escapeHtml(code.trim())}</code></pre>`;
  });

  // 行内代码 `...`
  html = html.replace(/`([^`]+)`/g, (_, code) => {
    return `<code class="ocean-md-inline-code">${escapeHtml(code)}</code>`;
  });

  // 标题
  html = html.replace(/^### (.+)$/gm, '<h3 class="ocean-md-h3">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 class="ocean-md-h2">$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1 class="ocean-md-h1">$1</h1>');

  // 粗体 **...**
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // 斜体 *...*
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  // 表格
  html = html.replace(/^\|(.+)\|\n\|([-| :]+)\|\n((?:\|.*\|\n?)*)/gm, (_, header, _sep, body) => {
    const headers = header.split('|').map((h: string) => h.trim()).filter(Boolean);
    const rows = body.trim().split('\n').map((r: string) => r.split('|').map((c: string) => c.trim()).filter(Boolean));
    let table = '<table class="ocean-md-table">';
    table += '<tr>' + headers.map((h: string) => `<th class="ocean-md-th">${h}</th>`).join('') + '</tr>';
    rows.forEach((row: string[]) => {
      table += '<tr>' + row.map((c: string) => `<td class="ocean-md-td">${c}</td>`).join('') + '</tr>';
    });
    table += '</table>';
    return table;
  });

  // 引用块 >
  html = html.replace(/^> (.+)$/gm, '<blockquote class="ocean-md-quote">$1</blockquote>');

  // 无序列表 - 或 *
  html = html.replace(/^[-*] (.+)$/gm, '<li class="ocean-md-li">$1</li>');
  html = html.replace(/(<li[^<]*<\/li>\n?)+/g, (m) => `<ul class="ocean-md-ul">${m}</ul>`);

  // 有序列表 1.
  html = html.replace(/^\d+\. (.+)$/gm, '<li class="ocean-md-li">$1</li>');

  // 分隔线 ---
  html = html.replace(/^---$/gm, '<hr class="ocean-md-hr" />');

  // 段落（把连续的非标签行用 p 包裹，排除 katex-math div 和其他块级元素）
  html = html.replace(/^(?!<[a-z/])(?<!<div class="katex-math")((?!<[a-z])(?!<div class="katex-math").+)$/gm, '<p class="ocean-md-p">$1</p>');

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
  user: '#1686AE',
  assistant: '#14765E',
  tool: '#9A6818',
};

/**
 * 消息角色 → 中文名
 */
const ROLE_LABEL: Record<string, string> = {
  user: '我',
  assistant: '小艾',
  tool: '工具',
};

/**
 * 消息列表组件
 *
 * 展示对话历史，区分用户消息、AI 回答与工具消息。
 * AI 回答使用 Markdown 渲染（含表格、公式、代码块等）。
 *
 * Data Ocean Phase 4：用语义 CSS class 替换硬编码颜色，
 * 保留 Markdown/KaTeX/ECharts/copy 行为不变。
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
                backgroundColor: ROLE_COLOR[msg.role] ?? '#1686AE',
                flexShrink: 0,
                fontSize: 14,
                fontWeight: 600,
              }}
            >
              {ROLE_AVATAR_TEXT[msg.role] ?? 'AI'}
            </Avatar>
            <div
              className={isUser ? 'ocean-msg-bubble ocean-msg-bubble--user' : 'ocean-msg-bubble ocean-msg-bubble--assistant'}
              style={{
                maxWidth: '75%',
                padding: '12px 16px',
                borderRadius: 12,
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
