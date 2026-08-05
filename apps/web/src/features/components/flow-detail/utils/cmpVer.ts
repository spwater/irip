/**
 * 版本号比较纯函数。
 *
 * 从 FlowDetail.tsx 提取。
 * 比较语义化版本号，返回 >0/0/<0。
 *
 * 注意：shared.ts 中已有 compareSemver 函数实现相同逻辑，
 * 此处保留 cmpVer 是为保持 FlowDetail 内部调用不变。
 */

/**
 * 比较两个版本号字符串。
 *
 * @param a - 版本号 a（如 "1.2.3"）
 * @param b - 版本号 b（如 "1.2.4"）
 * @returns >0 表示 a > b，0 表示相等，<0 表示 a < b
 */
export function cmpVer(a: string, b: string): number {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const va = pa[i] ?? 0;
    const vb = pb[i] ?? 0;
    if (va !== vb) return va - vb;
  }
  return 0;
}
