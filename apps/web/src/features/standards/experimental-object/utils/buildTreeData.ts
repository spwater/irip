/**
 * buildTreeData — 构建树形表格数据 + 类型行判断。
 *
 * 从 ExperimentalObjectPage.tsx 提取。纯函数，无副作用。
 */

import type { IndustrialObject } from '@/api/types';
import type { ObjectTypeDictItem } from '@/api/standards-objects';
import type { TreeRow } from '../types';

/**
 * 构建树形数据：第一层是类型，第二层是对象。
 *
 * - 按 object_type 分组对象
 * - 遍历类型字典，为每个有对象的类型生成类型行（id 以 type_ 前缀）
 * - 未在类型字典中的对象，在无类型筛选时直接挂到顶层
 */
export function buildTreeData(
  filteredItems: IndustrialObject[],
  objectTypeData: ObjectTypeDictItem[] | undefined,
  typeFilter: string | undefined,
): TreeRow[] {
  // 按 object_type 分组
  const typeMap = new Map<string, IndustrialObject[]>();
  for (const item of filteredItems) {
    const list = typeMap.get(item.object_type) ?? [];
    list.push(item);
    typeMap.set(item.object_type, list);
  }
  const tree: TreeRow[] = [];
  for (const typeItem of objectTypeData ?? []) {
    if (typeFilter && typeItem.code !== typeFilter) continue;
    const objs = typeMap.get(typeItem.code);
    if (objs && objs.length > 0) {
      // 对象行（不再挂载接口子行）
      const objRows: TreeRow[] = objs.map((obj) => ({ ...obj }) as TreeRow);
      tree.push({
        id: `type_${typeItem.code}`,
        code: typeItem.code,
        display_name: typeItem.display_name,
        object_type: typeItem.code,
        description: typeItem.description,
        status: '',
        department_id: null,
        component_id: null,
        visible_departments: [],
        visibility_scope: 'tree',
        owner_user_id: null,
        created_at: '',
        updated_at: '',
        lock_version: 0,
        children: objRows,
      } as TreeRow);
      typeMap.delete(typeItem.code);
    }
  }
  if (!typeFilter) {
    for (const [, objs] of typeMap) {
      for (const obj of objs) {
        tree.push({ ...obj } as TreeRow);
      }
    }
  }
  return tree;
}

/** 判断是否为类型行（第一层，id 以 type_ 前缀） */
export function isTypeRow(record: TreeRow): boolean {
  return record.id.startsWith('type_');
}
