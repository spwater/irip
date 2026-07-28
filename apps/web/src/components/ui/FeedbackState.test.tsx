import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FeedbackState, type FeedbackStateProps } from './FeedbackState';

describe('FeedbackState', () => {
  it.each([
    ['empty', '暂无实验记录'],
    ['forbidden', '无权访问此内容'],
    ['partial', '部分数据未加载'],
  ] as const)('renders %s with visible meaning', (kind, text) => {
    const props = { kind, title: text } as FeedbackStateProps;
    render(<FeedbackState {...props} />);
    expect(screen.getByText(text)).toBeVisible();
  });

  it('offers a real retry action for errors', async () => {
    const retry = vi.fn();
    render(<FeedbackState kind="error" title="加载失败" onRetry={retry} />);
    // Ant Design 会在两个中文字符之间自动插入空格（"重 试"），
    // 使用正则匹配以兼容此行为
    await userEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
