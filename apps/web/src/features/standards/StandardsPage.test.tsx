import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { PageHeaderProvider, usePageHeader } from '@/app/PageHeaderContext';
import { StandardsPage } from './StandardsPage';

// Mock child components
vi.mock('@/features/standards/ExperimentalObjectPage', () => ({
  ExperimentalObjectPage: () => <div data-testid="exp-objects-page">ExperimentalObjectPage</div>,
}));
vi.mock('@/features/governance/DepartmentManagement', () => ({
  DepartmentManagement: () => <div data-testid="dept-page">DepartmentManagement</div>,
}));
vi.mock('@/features/equipment/EquipmentPage', () => ({
  EquipmentPage: () => <div data-testid="equipment-page">EquipmentPage</div>,
}));

// Mock useNavigate and useSearch
const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useSearch: () => ({}),
}));

function HeaderProbe(): JSX.Element {
  const { header } = usePageHeader();
  return (
    <div>
      <span data-testid="header-title">{header.title ?? ''}</span>
      <div data-testid="header-tabs">
        {(header.tabs ?? []).map((t) => (
          <button key={t.key} data-testid={`tab-${t.key}`} onClick={() => header.onTabChange?.(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      <span data-testid="active-tab">{header.activeTab ?? ''}</span>
    </div>
  );
}

function renderPage(): void {
  render(
    <PageHeaderProvider>
      <AntApp>
        <HeaderProbe />
        <StandardsPage />
      </AntApp>
    </PageHeaderProvider>,
  );
}

describe('StandardsPage', () => {
  it('renders title 实验室建设 and three tabs', () => {
    renderPage();
    expect(screen.getByTestId('header-title')).toHaveTextContent('实验室建设');
    expect(screen.getByTestId('tab-departments')).toHaveTextContent('组织机构');
    expect(screen.getByTestId('tab-equipment')).toHaveTextContent('设备仪器');
    expect(screen.getByTestId('tab-exp-objects')).toHaveTextContent('实验对象');
  });

  it('defaults to departments tab and renders DepartmentManagement', () => {
    renderPage();
    expect(screen.getByTestId('active-tab')).toHaveTextContent('departments');
    expect(screen.getByTestId('dept-page')).toBeInTheDocument();
  });

  it('switches to equipment tab and renders EquipmentPage', async () => {
    renderPage();
    await userEvent.click(screen.getByTestId('tab-equipment'));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/standards',
      search: { tab: 'equipment' },
      replace: true,
    });
  });

  it('switches to exp-objects tab', async () => {
    renderPage();
    await userEvent.click(screen.getByTestId('tab-exp-objects'));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/standards',
      search: { tab: 'exp-objects' },
      replace: true,
    });
  });
});
