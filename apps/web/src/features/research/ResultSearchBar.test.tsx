import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { ResultSearchBar, type SearchResultFilters } from './ResultSearchBar';

const defaultFilters: SearchResultFilters = {
  query: '',
  tags: '',
  date_from: null,
  date_to: null,
  data_type: null,
  semantic: false,
};

/** Stateful wrapper that maintains filter state across onChange calls */
function StatefulSearchBar({
  onSearch,
  onReset,
}: {
  onSearch?: () => void;
  onReset?: () => void;
}): JSX.Element {
  const [filters, setFilters] = useState<SearchResultFilters>(defaultFilters);
  return (
    <ResultSearchBar
      filters={filters}
      onChange={setFilters}
      onSearch={onSearch ?? vi.fn()}
      onReset={onReset ?? vi.fn()}
    />
  );
}

function renderSearchBar(
  overrides: {
    filters?: Partial<SearchResultFilters>;
    onChange?: (f: SearchResultFilters) => void;
    onSearch?: () => void;
    onReset?: () => void;
  } = {},
): void {
  const filters = { ...defaultFilters, ...overrides.filters };
  const onChange = overrides.onChange ?? vi.fn();
  const onSearch = overrides.onSearch ?? vi.fn();
  const onReset = overrides.onReset ?? vi.fn();
  render(
    <ResultSearchBar
      filters={filters}
      onChange={onChange}
      onSearch={onSearch}
      onReset={onReset}
    />,
  );
}

describe('ResultSearchBar', () => {
  it('renders search input with placeholder', () => {
    renderSearchBar();
    expect(screen.getByPlaceholderText('搜索成果包标题、摘要或标签…')).toBeInTheDocument();
  });

  it('renders search and reset buttons', () => {
    renderSearchBar();
    expect(screen.getByRole('button', { name: /搜\s*索/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重\s*置/ })).toBeInTheDocument();
  });

  it('calls onChange with updated query when typing in search input', async () => {
    render(<StatefulSearchBar />);
    const input = screen.getByPlaceholderText('搜索成果包标题、摘要或标签…');
    await userEvent.type(input, '烧结');
    // After typing, the input should display the full text
    expect(screen.getByDisplayValue('烧结')).toBeInTheDocument();
  });

  it('calls onSearch when search button clicked', async () => {
    const onSearch = vi.fn();
    renderSearchBar({ onSearch });
    const searchBtn = screen.getByRole('button', { name: /搜\s*索/ });
    await userEvent.click(searchBtn);
    expect(onSearch).toHaveBeenCalled();
  });

  it('calls onReset when reset button clicked', async () => {
    const onReset = vi.fn();
    renderSearchBar({ onReset });
    const resetBtn = screen.getByRole('button', { name: /重\s*置/ });
    await userEvent.click(resetBtn);
    expect(onReset).toHaveBeenCalled();
  });

  it('displays current query value in input', () => {
    renderSearchBar({ filters: { query: '高温合金' } });
    expect(screen.getByDisplayValue('高温合金')).toBeInTheDocument();
  });

  it('renders tags input with placeholder', () => {
    renderSearchBar();
    expect(screen.getByPlaceholderText('标签筛选（逗号分隔）')).toBeInTheDocument();
  });
});
