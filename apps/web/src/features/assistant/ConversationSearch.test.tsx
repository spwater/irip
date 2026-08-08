import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConversationSearch } from './ConversationSearch';

describe('ConversationSearch', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  it('renders input with placeholder', () => {
    render(<ConversationSearch onSearch={vi.fn()} />);
    expect(screen.getByPlaceholderText('搜索对话标题或内容...')).toBeInTheDocument();
  });

  it('calls onSearch with empty string initially after debounce', async () => {
    vi.useRealTimers();
    const onSearch = vi.fn();
    render(<ConversationSearch onSearch={onSearch} />);
    await waitFor(() => {
      expect(onSearch).toHaveBeenCalledWith('');
    });
  });

  it('debounces search and calls onSearch with trimmed value', async () => {
    vi.useRealTimers();
    const onSearch = vi.fn();
    render(<ConversationSearch onSearch={onSearch} />);
    // Clear initial call
    await waitFor(() => expect(onSearch).toHaveBeenCalledWith(''));
    onSearch.mockClear();

    const input = screen.getByPlaceholderText('搜索对话标题或内容...');
    await userEvent.type(input, '烧结');
    await waitFor(() => {
      expect(onSearch).toHaveBeenCalledWith('烧结');
    });
  });

  it('trims whitespace from search value', async () => {
    vi.useRealTimers();
    const onSearch = vi.fn();
    render(<ConversationSearch onSearch={onSearch} />);
    await waitFor(() => expect(onSearch).toHaveBeenCalledWith(''));
    onSearch.mockClear();

    const input = screen.getByPlaceholderText('搜索对话标题或内容...');
    await userEvent.type(input, '  hello  ');
    await waitFor(() => {
      expect(onSearch).toHaveBeenCalledWith('hello');
    });
  });

  it('clears search when input is emptied', async () => {
    vi.useRealTimers();
    const onSearch = vi.fn();
    render(<ConversationSearch onSearch={onSearch} />);
    await waitFor(() => expect(onSearch).toHaveBeenCalledWith(''));
    onSearch.mockClear();

    const input = screen.getByPlaceholderText('搜索对话标题或内容...');
    await userEvent.type(input, 'test');
    await waitFor(() => expect(onSearch).toHaveBeenCalledWith('test'));
    onSearch.mockClear();

    await userEvent.clear(input);
    await waitFor(() => {
      expect(onSearch).toHaveBeenCalledWith('');
    });
  });
});
