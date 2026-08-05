/**
 * React 节点文本提取纯函数。
 *
 * 从 MessageThread.tsx 提取。包含：
 * - extractTextFromNode: 从 React 节点中提取纯文本内容
 * - rebuildTableMarkdown: 从 React <table> 子节点重建 Markdown 表格文本
 */

import type { ReactNode } from 'react';

/**
 * 从 React 节点中提取纯文本内容（用于 content_snapshot）。
 */
export function extractTextFromNode(node: ReactNode): string {
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
 * 从 React <table> 子节点重建 Markdown 表格文本。
 * 回退方案：当 tableSnapshots 匹配不到时，从渲染后的 table 节点反向构建 Markdown。
 */
export function rebuildTableMarkdown(node: ReactNode): string {
  const extractCellText = (cell: ReactNode): string => {
    const text = extractTextFromNode(cell).trim();
    return text || ' ';
  };

  const processRow = (row: ReactNode): string[] | null => {
    if (row === null || row === undefined) return null;
    if (typeof row === 'string' || typeof row === 'number') return null;
    if (Array.isArray(row)) {
      const cells: string[] = [];
      for (const child of row) {
        const rowCells = processRow(child);
        if (rowCells) cells.push(...rowCells);
      }
      return cells.length > 0 ? cells : null;
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if (typeof row === 'object' && row !== null && 'props' in (row as any)) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const props = (row as any).props as { children?: ReactNode };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const tag = (row as any).type;
      // tr 元素
      if (tag === 'tr' || (typeof tag === 'string' && tag === 'tr')) {
        const cells: string[] = [];
        const children = props.children;
        if (Array.isArray(children)) {
          for (const child of children) {
            const cellText = extractCellText(child);
            if (cellText) cells.push(cellText);
          }
        } else {
          const cellText = extractCellText(children);
          if (cellText) cells.push(cellText);
        }
        return cells.length > 0 ? cells : null;
      }
      // thead/tbody/tfoot → 递归子节点
      const result = processRow(props.children);
      return result;
    }
    return null;
  };

  const rows: string[][] = [];
  const flatten = (n: ReactNode) => {
    if (Array.isArray(n)) {
      n.forEach(flatten);
      return;
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if (typeof n === 'object' && n !== null && 'props' in (n as any)) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const tag = (n as any).type;
      if (tag === 'tr' || (typeof tag === 'string' && tag === 'tr')) {
        const cells = processRow(n);
        if (cells) rows.push(cells);
      } else {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        flatten((n as any).props?.children);
      }
    }
  };
  flatten(node);

  if (rows.length === 0) return extractTextFromNode(node);

  // 构建 Markdown 表格
  const colCount = Math.max(...rows.map(r => r.length));
  // 补齐每行列数
  const normRows = rows.map(r => {
    while (r.length < colCount) r.push(' ');
    return r;
  });

  const header = `| ${normRows[0].join(' | ')} |`;
  const separator = `| ${normRows[0].map(() => '---').join(' | ')} |`;
  const dataRows = normRows.slice(1).map(r => `| ${r.join(' | ')} |`);

  return [header, separator, ...dataRows].join('\n');
}
