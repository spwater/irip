import { describe, expect, it } from 'vitest';
import { canManage } from './canManage';
import type { CanManageUser } from '../types';
import type { FlowSummary } from '@/api/equipment-flows';

function makeUser(
  id: string,
  roles: string[],
  departmentId = 'dept-1',
): CanManageUser {
  return { id, roles, departmentId } as CanManageUser;
}

function makeFlow(
  owner_user_id: string | null,
  department_id: string = 'dept-1',
): FlowSummary {
  return { id: 'f1', owner_user_id, department_id } as unknown as FlowSummary;
}

describe('canManage', () => {
  it('returns false when flow is null', () => {
    expect(canManage(null, makeUser('u1', ['researcher']))).toBe(false);
  });

  it('returns false when user is null', () => {
    expect(canManage(makeFlow('u1'), null)).toBe(false);
  });

  it('returns true for platform_administrator regardless of ownership', () => {
    const user = makeUser('u-other', ['platform_administrator']);
    const flow = makeFlow('someone-else');
    expect(canManage(flow, user)).toBe(true);
  });

  it('returns true when user is the owner', () => {
    const user = makeUser('u1', ['researcher']);
    const flow = makeFlow('u1');
    expect(canManage(flow, user)).toBe(true);
  });

  it('returns false when user is not owner and not admin', () => {
    const user = makeUser('u2', ['researcher']);
    const flow = makeFlow('u1');
    expect(canManage(flow, user)).toBe(false);
  });

  it('returns true for lab_director in same department', () => {
    const user = makeUser('u-director', ['lab_director'], 'dept-1');
    const flow = makeFlow('u-worker', 'dept-1');
    expect(canManage(flow, user)).toBe(true);
  });

  it('returns false for lab_director in different department', () => {
    const user = makeUser('u-director', ['lab_director'], 'dept-2');
    const flow = makeFlow('u-worker', 'dept-1');
    expect(canManage(flow, user)).toBe(false);
  });

  it('returns false when flow has no owner and user is not admin/director', () => {
    const user = makeUser('u1', ['researcher']);
    const flow = makeFlow(null);
    expect(canManage(flow, user)).toBe(false);
  });
});
