import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import {
  apiListEquipment,
  apiGetEquipment,
  apiCreateEquipment,
  apiDeleteEquipment,
  apiUpdateEquipment,
  apiUpdateEquipmentStatus,
  type EquipmentListItem,
} from '@/api/equipment-flows';
import { apiListDepartments } from '@/api/departments';
import { EquipmentPage } from './EquipmentPage';

vi.mock('@/api/equipment-flows', () => ({
  apiListEquipment: vi.fn(),
  apiGetEquipment: vi.fn(),
  apiCreateEquipment: vi.fn(),
  apiDeleteEquipment: vi.fn(),
  apiUpdateEquipment: vi.fn(),
  apiUpdateEquipmentStatus: vi.fn(),
}));

vi.mock('@/api/departments', () => ({
  apiListDepartments: vi.fn(),
}));

vi.mock('@/features/standards/ExperimentalObjectPage', () => ({
  ExperimentalObjectPage: () => <div data-testid="exp-objects">ExperimentalObjectPage</div>,
}));

vi.mock('@/shared/DepartmentSelector', () => ({
  DepartmentSelector: ({ placeholder }: { placeholder?: string }) => (
    <select data-testid="dept-selector" aria-label={placeholder ?? '所属机构'}>
      <option value="">请选择</option>
      <option value="d1">实验室A</option>
    </select>
  ),
}));

vi.mock('@/shared/buildDeptTree', () => ({
  buildDeptTree: () => [],
}));

const mockEquipment: EquipmentListItem[] = [
  { id: 'e1', code: 'EQ-001', display_name: '光谱仪', description: 'X射线荧光光谱仪', department_id: 'd1', department_name: '实验室A', visible_departments: [], status: 'active', sort_order: 0 },
  { id: 'e2', code: 'EQ-002', display_name: '显微镜', description: null, department_id: 'd1', department_name: '实验室A', visible_departments: [], status: 'disabled', sort_order: 1 },
];

function renderPage(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <EquipmentPage />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe('EquipmentPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListEquipment).mockResolvedValue({ items: mockEquipment, next_cursor: null, has_more: false });
    vi.mocked(apiGetEquipment).mockResolvedValue({
      id: 'e1', department_id: 'd1', code: 'EQ-001', display_name: '光谱仪',
      description: 'X射线荧光光谱仪', visible_departments: [], status: 'active',
      sort_order: 0, created_at: '', updated_at: '', lock_version: 0,
    });
    vi.mocked(apiCreateEquipment).mockResolvedValue({
      id: 'e3', department_id: 'd1', code: 'EQ-003', display_name: '新设备',
      description: null, visible_departments: [], status: 'active',
      sort_order: 0, created_at: '', updated_at: '', lock_version: 0,
    });
    vi.mocked(apiUpdateEquipment).mockResolvedValue({
      id: 'e1', department_id: 'd1', code: 'EQ-001', display_name: '光谱仪',
      description: '更新描述', visible_departments: [], status: 'active',
      sort_order: 0, created_at: '', updated_at: '', lock_version: 0,
    });
    vi.mocked(apiUpdateEquipmentStatus).mockResolvedValue({
      id: 'e1', department_id: 'd1', code: 'EQ-001', display_name: '光谱仪',
      description: null, visible_departments: [], status: 'disabled',
      sort_order: 0, created_at: '', updated_at: '', lock_version: 0,
    });
    vi.mocked(apiDeleteEquipment).mockResolvedValue(undefined);
    vi.mocked(apiListDepartments).mockResolvedValue({
      items: [{ id: 'd1', code: 'lab-a', display_name: '实验室A', description: null, status: 'active', sort_order: 0, member_count: 5, parent_id: null, children_count: 0, equipment_count: 1 }],
      next_cursor: null,
      has_more: false,
    });
  });

  it('renders 新建仪器或方法 button', () => {
    renderPage();
    expect(screen.getByRole('button', { name: /新建仪器或方法/ })).toBeInTheDocument();
  });

  it('renders status filter and department filter', async () => {
    renderPage();
    // The Select components render with default '全部' option visible
    await screen.findByText('光谱仪');
    const allTexts = screen.getAllByText('全部');
    expect(allTexts.length).toBeGreaterThanOrEqual(1);
  });

  it('renders equipment list with names', async () => {
    renderPage();
    expect(await screen.findByText('光谱仪')).toBeInTheDocument();
    expect(screen.getByText('显微镜')).toBeInTheDocument();
  });

  it('shows status tags for equipment', async () => {
    renderPage();
    await screen.findByText('光谱仪');
    // Multiple "启用" and "禁用" texts expected (filter option + tag)
    const enabledElements = screen.getAllByText('启用');
    expect(enabledElements.length).toBeGreaterThanOrEqual(1);
    const disabledElements = screen.getAllByText('禁用');
    expect(disabledElements.length).toBeGreaterThanOrEqual(1);
  });

  it('opens create modal when 新建仪器或方法 clicked', async () => {
    renderPage();
    await screen.findByText('光谱仪');
    const buttons = screen.getAllByRole('button', { name: /新建仪器或方法/ });
    await userEvent.click(buttons[0]);
    // After clicking, the modal should be open
    expect(screen.getByText('设备名称')).toBeInTheDocument();
  });

  it('renders edit and object buttons in action column', async () => {
    renderPage();
    await screen.findByText('光谱仪');
    // Multiple "编辑" texts expected (one per row)
    const editButtons = screen.getAllByText('编辑');
    expect(editButtons.length).toBeGreaterThanOrEqual(1);
    const objectButtons = screen.getAllByText('+对象');
    expect(objectButtons.length).toBeGreaterThanOrEqual(1);
  });
});
