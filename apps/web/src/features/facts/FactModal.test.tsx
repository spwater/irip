import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { FactModal } from './FactModal';

vi.mock('@/api/client', () => ({
  http: {
    post: vi.fn(),
    patch: vi.fn(),
    get: vi.fn(),
    delete: vi.fn(),
    put: vi.fn(),
  },
  setAccessToken: vi.fn(),
  getAccessToken: vi.fn(() => null),
}));

vi.mock('@/shared/DepartmentSelector', () => ({
  DepartmentSelector: ({ allowRoot, placeholder }: { allowRoot?: boolean; placeholder?: string }) => (
    <select data-testid="dept-selector" aria-label={placeholder ?? '归属部门'}>
      <option value="">请选择</option>
      <option value="d1">实验室A</option>
      {allowRoot && <option value="root">公共数据</option>}
    </select>
  ),
}));

vi.mock('@/shared/PublishPrivateToggle', () => ({
  PublishPrivateToggle: ({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) => (
    <label>
      <input type="checkbox" data-testid="private-toggle" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      发布为私有
    </label>
  ),
}));

function renderModal(props: {
  open?: boolean;
  onClose?: () => void;
  onSuccess?: () => void;
  factId?: string;
}): void {
  render(
    <AntApp>
      <FactModal
        open={props.open ?? true}
        onClose={props.onClose ?? vi.fn()}
        onSuccess={props.onSuccess ?? vi.fn()}
        factId={props.factId}
      />
    </AntApp>,
  );
}

describe('FactModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().reset();
    useAuthStore.setState({
      user: { id: 'u-001', displayName: '研究员', roles: ['researcher'], permissions: [] },
    });
  });

  it('shows "新建实验数据" title when no factId', () => {
    renderModal({});
    expect(screen.getByText('新建实验数据')).toBeInTheDocument();
  });

  it('shows "编辑实验数据" title when factId provided', () => {
    renderModal({ factId: 'fact-001' });
    expect(screen.getByText('编辑实验数据')).toBeInTheDocument();
  });

  it('renders required form fields', () => {
    renderModal({});
    expect(screen.getByText('归属部门')).toBeInTheDocument();
    expect(screen.getByText('事实类型')).toBeInTheDocument();
    expect(screen.getByText('样品标识')).toBeInTheDocument();
  });

  it('shows validation error when submitting without required fields', async () => {
    renderModal({});
    const okButton = screen.getByRole('button', { name: /创\s*建/ });
    await userEvent.click(okButton);
    expect(await screen.findByText('请选择归属部门')).toBeInTheDocument();
  });

  it('shows validation errors for fact_type when submitting without selection', async () => {
    renderModal({});
    // Select department
    const deptSelector = screen.getByTestId('dept-selector');
    await userEvent.selectOptions(deptSelector, 'd1');

    // Enter subject ID
    const subjectInput = screen.getByPlaceholderText('如：样品编号、批次号');
    await userEvent.type(subjectInput, '样品-001');

    // Submit without selecting fact type
    const okButton = screen.getByRole('button', { name: /创\s*建/ });
    await userEvent.click(okButton);
    // Should show validation error for fact_type
    expect(await screen.findByText('请选择事实类型')).toBeInTheDocument();
  });

  it('calls onClose when cancel button clicked', async () => {
    const onClose = vi.fn();
    renderModal({ onClose });
    const cancelButton = screen.getByRole('button', { name: /取\s*消/ });
    await userEvent.click(cancelButton);
    expect(onClose).toHaveBeenCalled();
  });

  it('shows root option in department selector for admin', () => {
    useAuthStore.setState({
      user: { id: 'u-admin', displayName: '管理员', roles: ['platform_administrator'], permissions: [] },
    });
    renderModal({});
    const deptSelector = screen.getByTestId('dept-selector');
    const options = deptSelector.querySelectorAll('option');
    const optionValues = Array.from(options).map((o) => o.value);
    expect(optionValues).toContain('root');
  });
});
