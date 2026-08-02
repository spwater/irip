import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConversationTabs } from './ConversationTabs';

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('ConversationTabs', () => {
  it('renders two tab labels: 私有, 协同', () => {
    const onChange = vi.fn();
    renderWithClient(<ConversationTabs activeTab="private" onTabChange={onChange} />);
    expect(screen.getByText('私有')).toBeInTheDocument();
    expect(screen.getByText('协同')).toBeInTheDocument();
  });

  it('calls onTabChange with "collaborative" when 协同 clicked', async () => {
    const onChange = vi.fn<(tab: string) => void>();
    renderWithClient(<ConversationTabs activeTab="private" onTabChange={onChange} />);
    await userEvent.click(screen.getByText('协同'));
    expect(onChange).toHaveBeenCalledWith('collaborative');
  });

  it('calls onTabChange with "private" when 私有 clicked', async () => {
    const onChange = vi.fn<(tab: string) => void>();
    renderWithClient(<ConversationTabs activeTab="collaborative" onTabChange={onChange} />);
    await userEvent.click(screen.getByText('私有'));
    expect(onChange).toHaveBeenCalledWith('private');
  });
});
