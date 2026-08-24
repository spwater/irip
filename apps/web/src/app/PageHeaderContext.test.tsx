import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  PageHeaderProvider,
  usePageHeader,
  usePageHeaderRegistration,
} from './PageHeaderContext';

function HeaderConsumer() {
  const { header } = usePageHeader();
  return <div data-testid="header-state">{JSON.stringify(header)}</div>;
}

function PageWithRegistration({ title, tab }: { title: string; tab?: string }) {
  usePageHeaderRegistration(
    { title, tabs: [{ key: 'a', label: 'Tab A' }], activeTab: tab },
    [tab],
  );
  return <div>page-content</div>;
}

describe('PageHeaderContext', () => {
  it('PageHeaderProvider provides empty header by default', () => {
    render(
      <PageHeaderProvider>
        <HeaderConsumer />
      </PageHeaderProvider>,
    );
    expect(screen.getByTestId('header-state').textContent).toBe('{}');
  });

  it('usePageHeaderRegistration sets header on mount', () => {
    render(
      <PageHeaderProvider>
        <PageWithRegistration title="My Title" />
        <HeaderConsumer />
      </PageHeaderProvider>,
    );
    const state = screen.getByTestId('header-state').textContent;
    expect(state).toContain('"title":"My Title"');
  });

  it('usePageHeaderRegistration clears header on unmount', () => {
    const { unmount } = render(
      <PageHeaderProvider>
        <PageWithRegistration title="My Title" />
        <HeaderConsumer />
      </PageHeaderProvider>,
    );
    expect(screen.getByTestId('header-state').textContent).toContain('My Title');

    unmount();

    // After full unmount, both components are gone — re-render without the page
    render(
      <PageHeaderProvider>
        <HeaderConsumer />
      </PageHeaderProvider>,
    );
    expect(screen.getByTestId('header-state').textContent).toBe('{}');
  });

  it('usePageHeaderRegistration updates on dependency change', () => {
    const { rerender } = render(
      <PageHeaderProvider>
        <PageWithRegistration title="My Title" tab="a" />
        <HeaderConsumer />
      </PageHeaderProvider>,
    );

    let state = screen.getByTestId('header-state').textContent;
    expect(state).toContain('"activeTab":"a"');

    rerender(
      <PageHeaderProvider>
        <PageWithRegistration title="My Title" tab="b" />
        <HeaderConsumer />
      </PageHeaderProvider>,
    );

    state = screen.getByTestId('header-state').textContent;
    expect(state).toContain('"activeTab":"b"');
  });

  it('usePageHeader returns empty header and noop setHeader when used outside provider', () => {
    function OutsideProvider() {
      const { header, setHeader } = usePageHeader();
      return (
        <div>
          <span data-testid="header">{JSON.stringify(header)}</span>
          <button onClick={() => setHeader({ title: 'test' })}>set</button>
        </div>
      );
    }
    render(<OutsideProvider />);
    expect(screen.getByTestId('header').textContent).toBe('{}');
    // Clicking set should not throw
    userEvent.click(screen.getByText('set'));
    expect(screen.getByTestId('header').textContent).toBe('{}');
  });
});
