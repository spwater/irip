/**
 * ExperimentalObjectPage 局部类型和常量。
 *
 * 从 ExperimentalObjectPage.tsx 提取，供主组件和子组件共用。
 */

import type { IndustrialObject } from '@/api/types';

/** 树形行类型：第一层是类型，第二层是对象 */
export type TreeRow = IndustrialObject & { children?: TreeRow[] };

/** 状态 → 颜色 */
export const STATUS_COLOR: Record<string, string> = {
  active: 'green',
  inactive: 'default',
};

/** 状态 → 中文标签 */
export const STATUS_LABEL: Record<string, string> = {
  active: '启用',
  inactive: '禁用',
};

/** 实验对象类型选项（用于筛选下拉，供后续扩展使用） */
export const EXP_OBJECT_TYPES = [
  { value: '__all__', label: '全部' },
  { value: 'material', label: '物料' },
  { value: 'signal', label: '信号' },
];

/** 类型 → 中文标签（供后续扩展使用） */
export const TYPE_LABEL: Record<string, string> = {
  material: '物料',
  signal: '信号',
};

/** 列表查询用的类型过滤（供后续扩展使用） */
export const LIST_TYPE_FILTER = 'material,signal';
