import { describe, expect, it } from 'vitest';
import type { IndustrialObject } from '@/api/types';
import type { ObjectTypeDictItem } from '@/api/standards-objects';
import { buildTreeData, isTypeRow } from './experimental-object/utils/buildTreeData';

const typeMaterial: ObjectTypeDictItem = {
  id: 'type-001',
  code: 'material',
  display_name: '物料',
  description: '物料类型',
  sort_order: 0,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const typeSignal: ObjectTypeDictItem = {
  id: 'type-002',
  code: 'signal',
  display_name: '信号',
  description: '信号类型',
  sort_order: 1,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const materialObj1: IndustrialObject = {
  id: 'obj-001',
  code: 'MAT-001',
  display_name: '水泥熟料',
  object_type: 'material',
  description: '水泥熟料样品',
  status: 'active',
  equipment_id: null,
  component_id: null,
  department_id: 'dept-001',
  visible_departments: [],
  visibility_scope: 'tree',
  owner_user_id: 'user-001',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  lock_version: 1,
};

const materialObj2: IndustrialObject = {
  ...materialObj1,
  id: 'obj-002',
  code: 'MAT-002',
  display_name: '矿渣',
};

const signalObj: IndustrialObject = {
  ...materialObj1,
  id: 'obj-003',
  code: 'SIG-001',
  display_name: '温度信号',
  object_type: 'signal',
};

const unknownTypeObj: IndustrialObject = {
  ...materialObj1,
  id: 'obj-004',
  code: 'UNK-001',
  display_name: '未知类型对象',
  object_type: 'unknown_type',
};

describe('buildTreeData', () => {
  it('groups objects by type into tree rows', () => {
    const items = [materialObj1, materialObj2, signalObj];
    const result = buildTreeData(items, [typeMaterial, typeSignal], undefined);
    // Two type rows
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe('type_material');
    expect(result[0].children).toHaveLength(2);
    expect(result[1].id).toBe('type_signal');
    expect(result[1].children).toHaveLength(1);
  });

  it('sets type row display_name from type dict', () => {
    const result = buildTreeData([materialObj1], [typeMaterial], undefined);
    expect(result[0].display_name).toBe('物料');
  });

  it('filters by type when typeFilter is provided', () => {
    const items = [materialObj1, signalObj];
    const result = buildTreeData(items, [typeMaterial, typeSignal], 'material');
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('type_material');
  });

  it('returns empty array when no objects', () => {
    const result = buildTreeData([], [typeMaterial, typeSignal], undefined);
    expect(result).toEqual([]);
  });

  it('returns empty array when no type dict and no typeFilter', () => {
    const result = buildTreeData([materialObj1], undefined, undefined);
    // Unknown type objects without typeFilter are added as top-level rows
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('obj-001');
  });

  it('attaches objects with unknown types as top-level when no typeFilter', () => {
    const items = [materialObj1, unknownTypeObj];
    const result = buildTreeData(items, [typeMaterial], undefined);
    // type_material row + unknown top-level
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe('type_material');
    expect(result[1].id).toBe('obj-004');
  });

  it('excludes unknown type objects when typeFilter is set', () => {
    const items = [materialObj1, unknownTypeObj];
    const result = buildTreeData(items, [typeMaterial], 'material');
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('type_material');
  });

  it('skips types with no matching objects', () => {
    const items = [materialObj1];
    const result = buildTreeData(items, [typeMaterial, typeSignal], undefined);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('type_material');
  });

  it('sets type row status to empty string', () => {
    const result = buildTreeData([materialObj1], [typeMaterial], undefined);
    expect(result[0].status).toBe('');
  });
});

describe('isTypeRow', () => {
  it('returns true for rows with type_ prefix', () => {
    expect(isTypeRow({ ...materialObj1, id: 'type_material' })).toBe(true);
  });

  it('returns false for object rows', () => {
    expect(isTypeRow(materialObj1)).toBe(false);
  });

  it('returns false for rows without type_ prefix', () => {
    expect(isTypeRow({ ...materialObj1, id: 'obj-001' })).toBe(false);
  });
});
