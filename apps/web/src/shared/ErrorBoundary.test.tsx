import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ErrorBoundary from './ErrorBoundary';

function ThrowOnRender({ message }: { message: string }): never {
  throw new Error(message);
}

describe('ErrorBoundary', () => {
  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <div>child-content</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText('child-content')).toBeInTheDocument();
  });

  it('renders error UI when child throws', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <ThrowOnRender message="test-crash" />
      </ErrorBoundary>,
    );
    expect(screen.getByText('应用发生错误')).toBeInTheDocument();
    expect(screen.getByText('请刷新页面重试，或联系管理员。')).toBeInTheDocument();
    spy.mockRestore();
  });

  it('shows refresh button that reloads the page', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const reloadSpy = vi.fn();
    Object.defineProperty(window, 'location', {
      value: { reload: reloadSpy },
      writable: true,
    });

    render(
      <ErrorBoundary>
        <ThrowOnRender message="crash" />
      </ErrorBoundary>,
    );

    const btn = screen.getByText('刷新页面');
    await userEvent.click(btn);
    expect(reloadSpy).toHaveBeenCalledOnce();

    spy.mockRestore();
  });
});
