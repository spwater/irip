import { describe, expect, it } from 'vitest';
import { ROLE_OPTIONS } from './userUtils';

describe('userUtils ROLE_OPTIONS', () => {
  it('contains platform_administrator option', () => {
    const option = ROLE_OPTIONS.find((o) => o.value === 'platform_administrator');
    expect(option).toBeDefined();
    expect(option!.label).toBe('平台管理员');
  });

  it('contains platform_auditor option', () => {
    const option = ROLE_OPTIONS.find((o) => o.value === 'platform_auditor');
    expect(option).toBeDefined();
    expect(option!.label).toBe('平台监督员（只读）');
  });

  it('contains lab_director option', () => {
    const option = ROLE_OPTIONS.find((o) => o.value === 'lab_director');
    expect(option).toBeDefined();
    expect(option!.label).toBe('实验室负责人');
  });

  it('contains lab_member option', () => {
    const option = ROLE_OPTIONS.find((o) => o.value === 'lab_member');
    expect(option).toBeDefined();
    expect(option!.label).toBe('实验室成员');
  });

  it('contains lab_viewer option', () => {
    const option = ROLE_OPTIONS.find((o) => o.value === 'lab_viewer');
    expect(option).toBeDefined();
    expect(option!.label).toBe('实验室成员（只读）');
  });

  it('has exactly 5 role options', () => {
    expect(ROLE_OPTIONS).toHaveLength(5);
  });

  it('all options have unique values', () => {
    const values = ROLE_OPTIONS.map((o) => o.value);
    expect(new Set(values).size).toBe(values.length);
  });

  it('all options have non-empty labels', () => {
    for (const option of ROLE_OPTIONS) {
      expect(option.label).toBeTruthy();
      expect(option.label.length).toBeGreaterThan(0);
    }
  });
});
