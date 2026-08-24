import { describe, expect, it } from 'vitest';
import { compactJson } from './json-utils';

describe('compactJson', () => {
  it('removes spaces after colons and commas', () => {
    const result = compactJson({ a: 1, b: 2 });
    expect(result).toBe('{"a":1,"b":2}');
  });

  it('handles nested objects', () => {
    const result = compactJson({ outer: { inner: 'val' } });
    expect(result).toBe('{"outer":{"inner":"val"}}');
  });

  it('handles arrays', () => {
    const result = compactJson({ items: [1, 2, 3] });
    expect(result).toBe('{"items":[1,2,3]}');
  });

  it('handles array of objects', () => {
    const result = compactJson({ data: [{ x: 1 }, { y: 2 }] });
    expect(result).toBe('{"data":[{"x":1},{"y":2}]}');
  });

  it('handles string values', () => {
    const result = compactJson({ msg: 'hello, world' });
    expect(result).toBe('{"msg":"hello, world"}');
  });

  it('handles null and boolean values', () => {
    const result = compactJson({ a: null, b: true, c: false });
    expect(result).toBe('{"a":null,"b":true,"c":false}');
  });

  it('handles empty objects and arrays', () => {
    expect(compactJson({})).toBe('{}');
    expect(compactJson([])).toBe('[]');
    expect(compactJson({ a: [], b: {} })).toBe('{"a":[],"b":{}}');
  });
});
