import { describe, expect, it, vi, beforeEach } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import {
  registerQueryClient,
  registerCleanupCallback,
  clearSessionState,
} from './sessionState';

describe('sessionState', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('clearSessionState clears all irip: prefixed localStorage keys', () => {
    localStorage.setItem('irip:tenant1:user1:jobs', '[]');
    localStorage.setItem('irip:tenant2:user2:settings', '{}');
    localStorage.setItem('other-key', 'keep-me');

    clearSessionState();

    expect(localStorage.getItem('irip:tenant1:user1:jobs')).toBeNull();
    expect(localStorage.getItem('irip:tenant2:user2:settings')).toBeNull();
    expect(localStorage.getItem('other-key')).toBe('keep-me');
  });

  it('clearSessionState with scope only clears matching tenant+user keys', () => {
    localStorage.setItem('irip:org-a:user-1:jobs', '[]');
    localStorage.setItem('irip:org-b:user-2:jobs', '[]');
    localStorage.setItem('irip:org-a:user-1:settings', '{}');

    clearSessionState({ tenant: 'org-a', user: 'user-1' });

    expect(localStorage.getItem('irip:org-a:user-1:jobs')).toBeNull();
    expect(localStorage.getItem('irip:org-a:user-1:settings')).toBeNull();
    expect(localStorage.getItem('irip:org-b:user-2:jobs')).toBe('[]');
  });

  it('registerQueryClient + clearSessionState clears query cache', () => {
    const qc = new QueryClient();
    qc.setQueryData(['test'], { foo: 'bar' });
    registerQueryClient(qc);

    clearSessionState();

    expect(qc.getQueryData(['test'])).toBeUndefined();
  });

  it('registerCleanupCallback + clearSessionState invokes registered callbacks', () => {
    const cb = vi.fn();
    registerCleanupCallback(cb);

    clearSessionState();

    expect(cb).toHaveBeenCalledOnce();
  });

  it('clearSessionState continues if a cleanup callback throws', () => {
    const cb1 = vi.fn(() => {
      throw new Error('callback error');
    });
    const cb2 = vi.fn();
    registerCleanupCallback(cb1);
    registerCleanupCallback(cb2);

    clearSessionState();

    expect(cb1).toHaveBeenCalledOnce();
    expect(cb2).toHaveBeenCalledOnce();
  });
});
