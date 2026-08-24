import { describe, expect, it } from 'vitest';
import {
  fmtTime,
  compareVersions,
  yamlEscapeDouble,
  buildManifestYaml,
  parseYamlToFormValues,
  STATUS_COLOR,
  STATUS_LABEL,
  FRESH_FORM_VALUES,
  FORM_FIELD_NAMES,
} from './component-utils';

describe('fmtTime', () => {
  it('returns "-" for null/undefined/empty', () => {
    expect(fmtTime(null)).toBe('-');
    expect(fmtTime(undefined)).toBe('-');
    expect(fmtTime('')).toBe('-');
  });

  it('returns formatted local time for valid ISO string', () => {
    const result = fmtTime('2024-01-15T10:30:00Z');
    expect(result).not.toBe('-');
    expect(result).not.toBe('2024-01-15T10:30:00Z');
  });

  it('returns original value for invalid date string', () => {
    expect(fmtTime('not-a-date')).toBe('not-a-date');
  });
});

describe('compareVersions', () => {
  it('returns 0 for equal versions', () => {
    expect(compareVersions('1.2.3', '1.2.3')).toBe(0);
  });

  it('returns positive for a > b', () => {
    expect(compareVersions('2.0.0', '1.9.9')).toBeGreaterThan(0);
  });

  it('returns negative for a < b', () => {
    expect(compareVersions('1.0.0', '2.0.0')).toBeLessThan(0);
  });

  it('handles different segment counts', () => {
    expect(compareVersions('1.2', '1.2.0')).toBe(0);
    expect(compareVersions('1.2.1', '1.2')).toBeGreaterThan(0);
  });
});

describe('yamlEscapeDouble', () => {
  it('escapes backslashes', () => {
    expect(yamlEscapeDouble('a\\b')).toBe('a\\\\b');
  });

  it('escapes double quotes', () => {
    expect(yamlEscapeDouble('say "hello"')).toBe('say \\"hello\\"');
  });

  it('escapes newlines', () => {
    expect(yamlEscapeDouble('line1\nline2')).toBe('line1\\nline2');
  });

  it('escapes carriage returns', () => {
    expect(yamlEscapeDouble('a\rb')).toBe('a\\rb');
  });

  it('escapes tabs', () => {
    expect(yamlEscapeDouble('a\tb')).toBe('a\\tb');
  });

  it('handles empty string', () => {
    expect(yamlEscapeDouble('')).toBe('');
  });
});

describe('buildManifestYaml', () => {
  it('builds YAML with display_name and description', () => {
    const yaml = buildManifestYaml({
      display_name: 'Test Component',
      description: 'A test',
      prompt: 'Analyze data',
      tool_type: 'llm_converter',
    });
    expect(yaml).toContain('display_name: "Test Component"');
    expect(yaml).toContain('description: "A test"');
    expect(yaml).toContain('kind: ingestion');
    expect(yaml).toContain('name: iface_ffffffff');
  });

  it('uses original name when provided', () => {
    const yaml = buildManifestYaml(
      { display_name: 'T', description: 'D', prompt: 'P', tool_type: 'llm_converter' },
      'my_component',
    );
    expect(yaml).toContain('name: my_component');
    expect(yaml).not.toContain('iface_ffffffff');
  });

  it('includes prompt in default block', () => {
    const yaml = buildManifestYaml({
      display_name: 'T',
      description: 'D',
      prompt: 'Do the thing',
      tool_type: 'llm_converter',
    });
    expect(yaml).toContain('Do the thing');
  });

  it('escapes special characters in display_name', () => {
    const yaml = buildManifestYaml({
      display_name: 'Test "quoted"',
      description: 'D',
      prompt: 'P',
      tool_type: 'llm_converter',
    });
    expect(yaml).toContain('Test \\"quoted\\"');
  });
});

describe('parseYamlToFormValues', () => {
  it('extracts display_name and description', () => {
    const yaml = [
      'display_name: "My Component"',
      'description: "A description"',
      'version: 1.0.0',
    ].join('\n');
    const result = parseYamlToFormValues(yaml);
    expect(result.display_name).toBe('My Component');
    expect(result.description).toBe('A description');
  });

  it('extracts unquoted display_name', () => {
    const yaml = 'display_name: SimpleName\ndescription: Desc';
    const result = parseYamlToFormValues(yaml);
    expect(result.display_name).toBe('SimpleName');
  });

  it('returns empty object for empty yaml', () => {
    const result = parseYamlToFormValues('');
    expect(Object.keys(result)).toHaveLength(0);
  });

  it('extracts prompt from block scalar format', () => {
    const yaml = [
      'prompt:',
      '  type: string',
      '  description: "LLM 提示词"',
      '  default: |',
      '        Analyze the data',
    ].join('\n');
    const result = parseYamlToFormValues(yaml);
    expect(result.prompt).toBe('Analyze the data');
  });

  it('extracts tool_type from default value', () => {
    const yaml = [
      'tool_type:',
      '  type: string',
      '  description: "解析工具类型"',
      '  default: "xrd_converter"',
    ].join('\n');
    const result = parseYamlToFormValues(yaml);
    expect(result.tool_type).toBe('xrd_converter');
  });
});

describe('constants', () => {
  it('STATUS_COLOR has expected entries', () => {
    expect(STATUS_COLOR.draft).toBe('blue');
    expect(STATUS_COLOR.published).toBe('green');
    expect(STATUS_COLOR.deprecated).toBe('default');
  });

  it('STATUS_LABEL has Chinese labels', () => {
    expect(STATUS_LABEL.draft).toBe('草稿');
    expect(STATUS_LABEL.published).toBe('已发布');
    expect(STATUS_LABEL.deprecated).toBe('已弃用');
  });

  it('FRESH_FORM_VALUES has undefined display fields', () => {
    expect(FRESH_FORM_VALUES.display_name).toBeUndefined();
    expect(FRESH_FORM_VALUES.tool_type).toBe('llm_converter');
  });

  it('FORM_FIELD_NAMES contains expected fields', () => {
    expect(FORM_FIELD_NAMES).toContain('display_name');
    expect(FORM_FIELD_NAMES).toContain('description');
    expect(FORM_FIELD_NAMES).toContain('prompt');
    expect(FORM_FIELD_NAMES).toContain('tool_type');
  });
});
