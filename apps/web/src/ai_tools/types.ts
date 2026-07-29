/**
 * AI 工具管理 — 前端类型定义
 *
 * 与后端 AIToolDTO（apps/api/routers/ai_tools.py）字段对齐。
 */

/** AI 工具 DTO（列表 + 详情共用） */
export type AIToolDTO = {
  name: string;
  display_name: string;
  description: string;
  required_permission: string;
  parameters_schema: Record<string, unknown>;
  enabled: boolean;
  lock_version: number;
  updated_at: string;
  updated_by: string | null;
};

/** 工具筛选条件 */
export type ToolFilter = {
  /** 状态筛选：all=全部 / enabled=已启用 / disabled=已禁用 */
  status: 'all' | 'enabled' | 'disabled';
  /** 搜索关键词（匹配 name / display_name） */
  keyword: string;
};

/** 工具表单值（编辑抽屉用） */
export type ToolFormValues = {
  name: string;
  display_name: string;
  description: string;
  required_permission: string;
  /** parameters_schema 的 JSON 文本（TextArea 编辑用） */
  parameters_schema_text: string;
};
