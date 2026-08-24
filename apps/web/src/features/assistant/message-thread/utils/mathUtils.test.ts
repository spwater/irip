import { describe, expect, it } from 'vitest';
import { normalizeLatexMath, renderMath, preprocessMath } from './mathUtils';

describe('normalizeLatexMath', () => {
  it('converts \\[...\\] display math to $...$', () => {
    const input = 'Some text\n\\[\n\\frac{1}{2}\n\\]\nmore text';
    const result = normalizeLatexMath(input);
    expect(result).toContain('\\frac{1}{2}');
    expect(result).not.toContain('\\[');
    expect(result).not.toContain('\\]');
  });

  it('converts \\(...\\) inline math to $...$', () => {
    const input = 'The formula \\(\\frac{a}{b}\\) is inline';
    const result = normalizeLatexMath(input);
    expect(result).toContain('$\\frac{a}{b}$');
  });

  it('does not convert plain parentheses without LaTeX commands', () => {
    const input = 'This is (plain text) not math';
    const result = normalizeLatexMath(input);
    expect(result).toBe('This is (plain text) not math');
  });

  it('converts bracket-only display math when containing LaTeX commands', () => {
    const input = 'Text\n[\n\\sum_{i=0}^{n} x_i\n]\nEnd';
    const result = normalizeLatexMath(input);
    expect(result).toContain('\\sum_{i=0}^{n} x_i');
    expect(result).not.toMatch(/\[\s*\n/);
  });

  it('does not convert bracket-only without LaTeX commands (e.g. [1] references)', () => {
    const input = 'See reference [1] for details';
    const result = normalizeLatexMath(input);
    expect(result).toBe('See reference [1] for details');
  });

  it('handles single-line \\[...\\] at start of line', () => {
    const input = '\n\\[x^2 + y^2\\]\nsuffix';
    const result = normalizeLatexMath(input);
    expect(result).toContain('x^2 + y^2');
    expect(result).not.toContain('\\[');
    expect(result).not.toContain('\\]');
  });
});

describe('renderMath', () => {
  it('renders valid LaTeX to HTML string', () => {
    const html = renderMath('\\frac{1}{2}', true);
    expect(html).toContain('katex');
    expect(html).toContain('span');
  });

  it('renders inline math (displayMode=false)', () => {
    const html = renderMath('x^2', false);
    expect(html).toContain('katex');
  });

  it('renders invalid LaTeX with error color (throwOnError=false)', () => {
    const html = renderMath('\\undefinedcommand', true);
    // KaTeX with throwOnError=false renders errors with #cc0000 color
    expect(html).toContain('#cc0000');
  });
});

describe('preprocessMath', () => {
  it('extracts display math and replaces with placeholder', () => {
    const md = 'Hello $$x^2$$ world';
    const { html, mathMap } = preprocessMath(md);
    expect(mathMap.size).toBe(1);
    expect(html).not.toContain('$$x^2$$');
    expect(html).toMatch(/MATHDISPLAY\d+MATHEND/);
  });

  it('extracts inline math and replaces with placeholder', () => {
    const md = 'Value is $x + y$ here';
    const { html, mathMap } = preprocessMath(md);
    expect(mathMap.size).toBe(1);
    expect(html).not.toMatch(/\$x \+ y\$/);
    expect(html).toMatch(/MATHINLINE\d+MATHEND/);
  });

  it('handles multiple formulas in same text', () => {
    const md = '$$a$$ and $b$ and $$c$$';
    const { html, mathMap, formulaMap } = preprocessMath(md);
    expect(mathMap.size).toBe(3); // 2 display + 1 inline
    expect(formulaMap.size).toBe(2); // only display math goes to formulaMap
    expect(html).not.toContain('$$');
    expect(html).not.toMatch(/\$[a-z]\$/);
  });

  it('formulaMap preserves original formula text', () => {
    const md = '$$x^2 + y^2$$';
    const { formulaMap } = preprocessMath(md);
    const formulas = Array.from(formulaMap.values());
    expect(formulas[0]).toBe('$$x^2 + y^2$$');
  });
});
