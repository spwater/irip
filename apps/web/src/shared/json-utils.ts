/**
 * JSON 工具函数。
 *
 * compactJson：去掉 JSON 序列化后的多余空格，减少 token 消耗。
 * 用于发给 LLM 的 system_context 等场景，不用于前端展示（展示用 indent=2）。
 */

/**
 * 将对象序列化为紧凑 JSON 字符串（去掉所有多余空格）。
 *
 * JS 的 JSON.stringify 默认输出 `{"a": 1, "b": 2}`（冒号后、逗号后有空格），
 * 本函数输出 `{"a":1,"b":2}`，对于大段数据可减少约 10% 的 token 消耗。
 *
 * 仅用于发给 LLM 的数据，不用于前端展示（展示场景应用 indent=2 保持可读性）。
 */
export function compactJson(data: unknown): string {
  return JSON.stringify(data)
    .replace(/,\s*"/g, ',"')
    .replace(/:\s*"/g, ':"')
    .replace(/:\s*\[/g, ':[')
    .replace(/:\s*\{/g, ':{')
    .replace(/,\s*\[/g, ',[')
    .replace(/,\s*\{/g, ',{');
}
