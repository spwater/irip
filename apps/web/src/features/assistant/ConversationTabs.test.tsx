import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConversationTabs } from './ConversationTabs';
import type { ConversationTab } from '@/api/collaboration';

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe('ConversationTabs', () => {
  it('renders three tab labels: 私有, 同组织, 跨组织', () => {
    const onChange = vi.fn();
    renderWithClient(<ConversationTabs activeTab="private" onTabChange={onChange} />);
    expect(screen.getByText('私有')).toBeInTheDocument();
    expect(screen.getByText('同组织')).toBeInTheDocument();
    expect(screen.getByText('跨组织')).toBeInTheDocument();
  });

  it('calls onTabChange with "same_org" when 同组织 clicked', async () => {
    const onChange = vi.fn<(tab: ConversationTab) => void>();
    renderWithClient(<ConversationTabs activeTab="private" onTabChange={onChange} />);
    await userEvent.click(screen.getByText('同组织'));
    expect(onChange).toHaveBeenCalledWith('same_org');
  });

  it('calls onTabChange with "private" when 私有 clicked', async () => {
    const onChange = vi.fn<(tab: ConversationTab) => void>();
    renderWithClient(<ConversationTabs activeTab="same_org" onTabChange={onChange} />);
    await userEvent.click(screen.getByText('私有'));
    expect(onChange).toHaveBeenCalledWith('private');
  });

  it('does not call onTabChange when 跨组织 clicked (disabled)', async () => {
    const onChange = vi.fn<(tab: ConversationTab) => void>();
    renderWithClient(<ConversationTabs activeTab="private" onTabChange={onChange} />);
    // 跨组织 is disabled — clicking should not trigger onChange
    const crossOrg = screen.getByText('跨组织');
    await userEvent.click(crossOrg);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('renders tooltip text for cross_org on hover (二期上线)', async () => {
    const onChange = vi.fn();
    renderWithClient(<ConversationTabs activeTab="private" onTabChange={onChange} />);
    // Hover over 跨组织 to trigger Tooltip
    const crossOrg = screen.getByText('跨组织');
    await userEvent.hover(crossOrg);
    // Tooltip content appears after hover
    await waitFor(() => {
      expect(screen.getByText('跨组织协作功能将在二期上线')).toBeInTheDocument();
    });
  });
});
