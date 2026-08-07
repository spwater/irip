/**
 * LaTeX / KaTeX 数学公式处理纯函数。
 *
 * 从 MessageThread.tsx 提取。包含：
 * - normalizeLatexMath: 将各种数学公式语法统一转换为 $$...$$ 和 $...$ 语法
 * - renderMath: 用 KaTeX JS API 直接渲染公式为 HTML 字符串
 * - preprocessMath: 预处理 Markdown 中的公式，用占位符标记
 */

import katex from 'katex';

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
export function normalizeLatexMath(md: string): string {
  const hasLatexCmd = (s: string): boolean =>
    /\\(frac|sum|bar|hat|sqrt|int|oint|partial|nabla|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|sigma|omega|pi|infty|cdot|times|div|pm|mp|le|ge|ne|approx|equiv|propto|leq|geq|subset|supset|in|notin|cup|cap|forall|exists|mathbb|mathcal|mathbf|text|mathrm|left|right|begin|end|operatorname)/.test(
      s,
    );

  let result = md;
  // \[...\] 跨行 → $$...$$
  result = result.replace(
    /(^|\n)\s*\\\[\s*\n([\s\S]*?)\n\s*\\\]\s*(?=\n|$)/g,
    '$1\n$$ $2 $$\n',
  );
  // \[...\] 单行 → $$...$$
  result = result.replace(/(^|\n)\s*\\\[([\s\S]*?)\\\]\s*(?=\n|$)/g, '$1$$ $2 $$');
  // \(...\) → $...$
  result = result.replace(/\\\(([\s\S]*?)\\\)/g, (match, inner) => {
    if (hasLatexCmd(inner)) return `$${inner}$`;
    return match;
  });
  // 纯方括号 [ ... ] 独占行 → $$...$$（仅含 LaTeX 命令时）
  result = result.replace(
    /(^|\n)\s*\[\s*\n([\s\S]*?)\n\s*\]\s*(?=\n|$)/g,
    (match, prefix, inner) => {
      if (hasLatexCmd(inner)) return `${prefix}\n$$ ${inner.trim()} $$\n`;
      return match;
    },
  );
  result = result.replace(
    /(^|\n)\s*\[([\s\S]*?)\]\s*(?=\n|$)/g,
    (match, prefix, inner) => {
      if (hasLatexCmd(inner)) return `${prefix}$$ ${inner.trim()} $$`;
      return match;
    },
  );
  return result;
}

/**
 * 用 KaTeX JS API 直接渲染公式为 HTML 字符串。
 * 绕开 rehype-katex 的 hast 节点转换（在 react-markdown v10 中有兼容性问题）。
 */
export function renderMath(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex, {
      displayMode,
      throwOnError: false,
      strict: false,
    });
  } catch {
    const escaped = tex.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `<span style="color:red">${escaped}</span>`;
  }
}

/** preprocessMath 返回类型 */
export interface MathPreprocessResult {
  html: string;
  mathMap: Map<string, string>;
  formulaMap: Map<string, string>;
}

/**
 * 预处理：把 $$...$$ 和 $...$ 公式用 KaTeX 渲染成 HTML，
 * 然后用特殊占位符标记，后续在 ReactMarkdown 渲染后替换。
 *
 * 不用 rehype-katex，直接用 KaTeX JS API 生成 HTML 字符串。
 */
export function preprocessMath(md: string): MathPreprocessResult {
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
