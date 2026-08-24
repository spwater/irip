import { describe, expect, it } from 'vitest';
import { renderMentions } from './mentionUtils';
import { render } from '@testing-library/react';

describe('renderMentions', () => {
  it('returns plain content when mentions is empty', () => {
    const result = renderMentions('hello world', []);
    expect(result).toBe('hello world');
  });

  it('returns plain content when mentions is undefined', () => {
    const result = renderMentions('hello world', undefined);
    expect(result).toBe('hello world');
  });

  it('highlights @mention text with styled span', () => {
    const content = 'hello @john please check';
    const { container } = render(
      <>{renderMentions(content, ['john'])}</>,
    );
    const spans = container.querySelectorAll('span');
    expect(spans.length).toBeGreaterThan(0);
    expect(container.textContent).toContain('@john');
  });

  it('highlights multiple @mentions', () => {
    const content = '@alice and @bob are working';
    const { container } = render(
      <>{renderMentions(content, ['alice', 'bob'])}</>,
    );
    expect(container.textContent).toContain('@alice');
    expect(container.textContent).toContain('@bob');
    const spans = container.querySelectorAll('span');
    expect(spans.length).toBe(2);
  });

  it('preserves non-mention text', () => {
    const content = 'hello @john bye';
    const { container } = render(
      <>{renderMentions(content, ['john'])}</>,
    );
    expect(container.textContent).toBe('hello @john bye');
  });

  it('handles @mention at start of content', () => {
    const content = '@john starts the message';
    const { container } = render(
      <>{renderMentions(content, ['john'])}</>,
    );
    expect(container.textContent).toBe('@john starts the message');
    expect(container.querySelectorAll('span').length).toBe(1);
  });

  it('handles @mention at end of content', () => {
    const content = 'message ends with @john';
    const { container } = render(
      <>{renderMentions(content, ['john'])}</>,
    );
    expect(container.textContent).toBe('message ends with @john');
  });
});
