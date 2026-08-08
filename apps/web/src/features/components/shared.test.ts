import { describe, expect, it } from 'vitest';
import {
  STATUS_COLOR,
  STATUS_LABEL,
  RUN_STATUS_COLOR,
  RUN_STATUS_LABEL,
  fmtTime,
  compareSemver,
  genKey,
  parseScalarValue,
  isComplexType,
  convertParamValue,
  parseManifest,
} from './shared';

describe('shared constants', () => {
  it('STATUS_COLOR maps published to green', () => {
    expect(STATUS_COLOR.published).toBe('green');
  });

  it('STATUS_COLOR maps deprecated to default', () => {
    expect(STATUS_COLOR.deprecated).toBe('default');
  });

  it('STATUS_LABEL maps published to 已发布', () => {
    expect(STATUS_LABEL.published).toBe('已发布');
  });

  it('STATUS_LABEL maps deprecated to 已弃用', () => {
    expect(STATUS_LABEL.deprecated).toBe('已弃用');
  });

  it('RUN_STATUS_COLOR maps running to processing', () => {
    expect(RUN_STATUS_COLOR.running).toBe('processing');
  });

  it('RUN_STATUS_COLOR maps failed to red', () => {
    expect(RUN_STATUS_COLOR.failed).toBe('red');
  });

  it('RUN_STATUS_LABEL maps succeeded to 成功', () => {
    expect(RUN_STATUS_LABEL.succeeded).toBe('成功');
  });

  it('RUN_STATUS_LABEL maps pending to 等待中', () => {
    expect(RUN_STATUS_LABEL.pending).toBe('等待中');
  });
});

describe('fmtTime', () => {
  it('returns - for null', () => {
    expect(fmtTime(null)).toBe('-');
  });

  it('returns - for undefined', () => {
    expect(fmtTime(undefined)).toBe('-');
  });

  it('returns - for empty string', () => {
    expect(fmtTime('')).toBe('-');
  });

  it('formats a valid ISO date string', () => {
    const result = fmtTime('2024-01-15T10:30:00Z');
    expect(result).toMatch(/2024/);
    expect(result).not.toBe('-');
  });

  it('returns original string for invalid date', () => {
    expect(fmtTime('not-a-date')).toBe('not-a-date');
  });
});

describe('compareSemver', () => {
  it('returns 0 for equal versions', () => {
    expect(compareSemver('1.0.0', '1.0.0')).toBe(0);
  });

  it('returns positive when a > b', () => {
    expect(compareSemver('2.0.0', '1.0.0')).toBeGreaterThan(0);
  });

  it('returns negative when a < b', () => {
    expect(compareSemver('1.0.0', '2.0.0')).toBeLessThan(0);
  });

  it('handles different segment counts', () => {
    expect(compareSemver('1.2', '1.2.0')).toBe(0);
  });

  it('compares patch versions correctly', () => {
    expect(compareSemver('1.0.10', '1.0.2')).toBeGreaterThan(0);
  });
});

describe('genKey', () => {
  it('generates unique keys with vk_ prefix', () => {
    const key1 = genKey();
    const key2 = genKey();
    expect(key1).toMatch(/^vk_\d+_\d+$/);
    expect(key2).toMatch(/^vk_\d+_\d+$/);
    expect(key1).not.toBe(key2);
  });
});

describe('parseScalarValue', () => {
  it('parses true to boolean true', () => {
    expect(parseScalarValue('true')).toBe(true);
  });

  it('parses false to boolean false', () => {
    expect(parseScalarValue('false')).toBe(false);
  });

  it('parses null to null', () => {
    expect(parseScalarValue('null')).toBe(null);
  });

  it('parses tilde to null', () => {
    expect(parseScalarValue('~')).toBe(null);
  });

  it('parses integer string to number', () => {
    expect(parseScalarValue('42')).toBe(42);
  });

  it('parses float string to number', () => {
    expect(parseScalarValue('3.14')).toBe(3.14);
  });

  it('returns empty string for empty or quote-only input', () => {
    expect(parseScalarValue('')).toBe('');
    expect(parseScalarValue('""')).toBe('');
    expect(parseScalarValue("''")).toBe('');
  });

  it('strips surrounding quotes from string', () => {
    expect(parseScalarValue('"hello"')).toBe('hello');
    expect(parseScalarValue("'world'")).toBe('world');
  });

  it('returns plain string for non-special values', () => {
    expect(parseScalarValue('hello')).toBe('hello');
  });
});

describe('isComplexType', () => {
  it('returns true for array', () => {
    expect(isComplexType('array')).toBe(true);
  });

  it('returns true for object', () => {
    expect(isComplexType('object')).toBe(true);
  });

  it('returns true for dict', () => {
    expect(isComplexType('dict')).toBe(true);
  });

  it('returns true for list', () => {
    expect(isComplexType('list')).toBe(true);
  });

  it('returns true for map', () => {
    expect(isComplexType('map')).toBe(true);
  });

  it('returns false for string', () => {
    expect(isComplexType('string')).toBe(false);
  });

  it('returns false for int', () => {
    expect(isComplexType('int')).toBe(false);
  });

  it('is case insensitive', () => {
    expect(isComplexType('ARRAY')).toBe(true);
    expect(isComplexType('Object')).toBe(true);
  });
});

describe('convertParamValue', () => {
  it('converts int value to rounded integer', () => {
    expect(convertParamValue('42', 'int')).toBe(42);
    expect(convertParamValue('42.7', 'int')).toBe(43);
  });

  it('returns empty string for empty int input', () => {
    expect(convertParamValue('', 'int')).toBe('');
  });

  it('converts float value to number', () => {
    expect(convertParamValue('3.14', 'float')).toBe(3.14);
  });

  it('converts double value to number', () => {
    expect(convertParamValue('2.5', 'double')).toBe(2.5);
  });

  it('converts number value to number', () => {
    expect(convertParamValue('10', 'number')).toBe(10);
  });

  it('converts bool true values', () => {
    expect(convertParamValue('true', 'bool')).toBe(true);
    expect(convertParamValue('1', 'bool')).toBe(true);
  });

  it('converts bool false values', () => {
    expect(convertParamValue('false', 'bool')).toBe(false);
    expect(convertParamValue('0', 'bool')).toBe(false);
  });

  it('returns string for unknown type', () => {
    expect(convertParamValue('hello', 'string')).toBe('hello');
  });
});

describe('parseManifest', () => {
  it('returns empty result for empty string', () => {
    const result = parseManifest('');
    expect(result.params).toEqual([]);
    expect(result.inputs).toEqual([]);
    expect(result.outputs).toEqual([]);
  });

  it('returns empty result for non-string input', () => {
    const result = parseManifest(null as unknown as string);
    expect(result.params).toEqual([]);
  });

  it('parses inputs section', () => {
    const yaml = `inputs:\n  - name: temp\n    data_type: float\n  - name: pressure\n    data_type: float`;
    const result = parseManifest(yaml);
    expect(result.inputs).toHaveLength(2);
    expect(result.inputs[0].name).toBe('temp');
    expect(result.inputs[0].type).toBe('float');
    expect(result.inputs[1].name).toBe('pressure');
  });

  it('parses outputs section', () => {
    const yaml = `outputs:\n  - name: result\n    data_type: float`;
    const result = parseManifest(yaml);
    expect(result.outputs).toHaveLength(1);
    expect(result.outputs[0].name).toBe('result');
    expect(result.outputs[0].type).toBe('float');
  });

  it('parses empty inline list inputs: []', () => {
    const yaml = `inputs: []`;
    const result = parseManifest(yaml);
    expect(result.inputs).toEqual([]);
  });

  it('parses parameters with properties and required', () => {
    const yaml = [
      'parameters:',
      '  required:',
      '    - threshold',
      '  properties:',
      '    threshold:',
      '      type: float',
      '      default: 0.5',
      '      description: threshold value',
      '    optional_param:',
      '      type: string',
      '      default: hello',
    ].join('\n');
    const result = parseManifest(yaml);
    expect(result.params).toHaveLength(2);
    expect(result.params[0].name).toBe('threshold');
    expect(result.params[0].type).toBe('float');
    expect(result.params[0].required).toBe(true);
    expect(result.params[0].default).toBe(0.5);
    expect(result.params[0].description).toBe('threshold value');
    expect(result.params[1].name).toBe('optional_param');
    expect(result.params[1].required).toBe(false);
  });

  it('parses inline required list format: required: [a, b]', () => {
    const yaml = [
      'parameters:',
      '  required: [alpha, beta]',
      '  properties:',
      '    alpha:',
      '      type: int',
      '    beta:',
      '      type: string',
    ].join('\n');
    const result = parseManifest(yaml);
    expect(result.params[0].required).toBe(true);
    expect(result.params[1].required).toBe(true);
  });
});
