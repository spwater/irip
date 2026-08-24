import { describe, expect, it } from 'vitest';
import { extractTextFromNode, rebuildTableMarkdown } from './nodeUtils';
import type { ReactNode } from 'react';

describe('extractTextFromNode', () => {
  it('returns empty string for null/undefined/boolean', () => {
    expect(extractTextFromNode(null)).toBe('');
    expect(extractTextFromNode(undefined)).toBe('');
    expect(extractTextFromNode(true)).toBe('');
    expect(extractTextFromNode(false)).toBe('');
  });

  it('returns string as-is', () => {
    expect(extractTextFromNode('hello')).toBe('hello');
  });

  it('converts number to string', () => {
    expect(extractTextFromNode(42)).toBe('42');
  });

  it('concatenates array of nodes', () => {
    expect(extractTextFromNode(['a', 'b', 'c'])).toBe('abc');
  });

  it('extracts text from React element props.children', () => {
    const el = {
      type: 'span',
      props: { children: 'nested text' },
    };
    expect(extractTextFromNode(el as unknown as ReactNode)).toBe('nested text');
  });

  it('recursively extracts from nested elements', () => {
    const el = {
      type: 'div',
      props: {
        children: [
          { type: 'span', props: { children: 'hello' } },
          { type: 'span', props: { children: 'world' } },
        ],
      },
    };
    expect(extractTextFromNode(el as unknown as ReactNode)).toBe('helloworld');
  });

  it('returns empty string for unknown node type', () => {
    expect(extractTextFromNode({} as unknown as ReactNode)).toBe('');
  });
});

describe('rebuildTableMarkdown', () => {
  it('rebuilds a simple table with header and data row', () => {
    // Simulate a React table structure
    const table = {
      type: 'table',
      props: {
        children: [
          {
            type: 'thead',
            props: {
              children: {
                type: 'tr',
                props: {
                  children: [
                    { type: 'th', props: { children: 'Name' } },
                    { type: 'th', props: { children: 'Age' } },
                  ],
                },
              },
            },
          },
          {
            type: 'tbody',
            props: {
              children: {
                type: 'tr',
                props: {
                  children: [
                    { type: 'td', props: { children: 'Alice' } },
                    { type: 'td', props: { children: '30' } },
                  ],
                },
              },
            },
          },
        ],
      },
    };

    const md = rebuildTableMarkdown(table as unknown as ReactNode);
    expect(md).toContain('| Name | Age |');
    expect(md).toContain('| --- | --- |');
    expect(md).toContain('| Alice | 30 |');
  });

  it('handles table with multiple data rows', () => {
    const table = {
      type: 'table',
      props: {
        children: {
          type: 'tbody',
          props: {
            children: [
              {
                type: 'tr',
                props: {
                  children: [
                    { type: 'td', props: { children: 'A' } },
                    { type: 'td', props: { children: '1' } },
                  ],
                },
              },
              {
                type: 'tr',
                props: {
                  children: [
                    { type: 'td', props: { children: 'B' } },
                    { type: 'td', props: { children: '2' } },
                  ],
                },
              },
            ],
          },
        },
      },
    };

    const md = rebuildTableMarkdown(table as unknown as ReactNode);
    expect(md).toContain('A');
    expect(md).toContain('B');
    expect(md).toContain('---');
  });

  it('falls back to extractTextFromNode for non-table content', () => {
    const content = 'just plain text';
    expect(rebuildTableMarkdown(content as unknown as ReactNode)).toBe('just plain text');
  });
});
