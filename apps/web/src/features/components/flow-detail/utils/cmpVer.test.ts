import { describe, expect, it } from 'vitest';
import { cmpVer } from './cmpVer';

describe('cmpVer', () => {
  it('returns 0 for equal versions', () => {
    expect(cmpVer('1.2.3', '1.2.3')).toBe(0);
  });

  it('returns positive when a > b', () => {
    expect(cmpVer('1.2.4', '1.2.3')).toBeGreaterThan(0);
  });

  it('returns negative when a < b', () => {
    expect(cmpVer('1.2.2', '1.2.3')).toBeLessThan(0);
  });

  it('compares major version difference', () => {
    expect(cmpVer('2.0.0', '1.9.9')).toBeGreaterThan(0);
    expect(cmpVer('1.0.0', '2.0.0')).toBeLessThan(0);
  });

  it('handles different segment counts (missing segments default to 0)', () => {
    expect(cmpVer('1.2', '1.2.0')).toBe(0);
    expect(cmpVer('1.2.1', '1.2')).toBeGreaterThan(0);
    expect(cmpVer('1.2', '1.2.1')).toBeLessThan(0);
  });

  it('handles single segment versions', () => {
    expect(cmpVer('1', '1')).toBe(0);
    expect(cmpVer('2', '1')).toBeGreaterThan(0);
    expect(cmpVer('1', '2')).toBeLessThan(0);
  });

  it('compares multi-digit segments numerically', () => {
    expect(cmpVer('1.10.0', '1.9.0')).toBeGreaterThan(0);
    expect(cmpVer('1.9.0', '1.10.0')).toBeLessThan(0);
  });
});
