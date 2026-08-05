import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import type { CurrentUser } from '@/api/client';
import type { ParameterCandidate } from '@/api/types';
import { ApprovalPanel } from '@/features/parameters/ApprovalPanel';

// vi.mock 必须在 import 之前（vitest 会自动提升）
vi.mock('@/api/facts-provenance', () => ({
  apiApproveCandidate: vi.fn(),
  apiRejectCandidate: vi.fn(),
}));

vi.mock('@/api/types', () => ({
  extractApiError: (err: unknown) =>
    err instanceof Error ? err.message : '操作失败',
}));

// Mock useNavigate — Bug 1 修复后 ApprovalPanel 使用 useNavigate 跳转 /lab-ops
const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}));

const baseCandidate: ParameterCandidate = {
  id: 'cand-001',
  parameter_id: 'param-001',
  version_label: 'v1',
  value: 42,
  unit: 'MPa',
  conditions: null,
  confidence_interval: { lower: 40, upper: 44 },
  evidence_count: 5,
  quality_level: 'Q2',
  status: 'in_review',
  submitted_by: 'u-researcher-001',
  derivation_run_id: 'run-001',
};

const baseUser: CurrentUser = {
  id: 'u-researcher-001',
  displayName: '研究员',
  roles: ['researcher'],
  permissions: [],
};

/** 拥有审批权限的审核者 */
const reviewerUser: CurrentUser = {
  id: 'u-reviewer-002',
  displayName: '审核员',
  roles: ['reviewer'],
  permissions: ['parameter:approve'],
};

function renderApprovalPanel(props: {
  candidate: ParameterCandidate;
  currentUser: CurrentUser;
  parameterId: string;
}): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <ApprovalPanel {...props} />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe('ApprovalPanel', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
  });

  it('shows provenance but hides approval for the submitter', () => {
    // Current user IS the submitter — self_approval_forbidden
    renderApprovalPanel({
      candidate: baseCandidate,
      currentUser: { ...reviewerUser, id: 'u-researcher-001' },
      parameterId: 'param-001',
    });

    // "查看完整来源" link should be visible
    expect(
      screen.getByText(/查\s*看\s*完\s*整\s*来\s*源/),
    ).toBeVisible();

    // "批准发布" button should NOT be in the document (submitter cannot approve)
    expect(
      screen.queryByRole('button', { name: /批\s*准\s*发\s*布/ }),
    ).not.toBeInTheDocument();
  });

  it('shows both provenance and approval for an authorized reviewer', () => {
    // Current user is NOT the submitter AND has parameter:approve permission
    renderApprovalPanel({
      candidate: baseCandidate,
      currentUser: reviewerUser,
      parameterId: 'param-001',
    });

    // "查看完整来源" link should be visible
    expect(
      screen.getByText(/查\s*看\s*完\s*整\s*来\s*源/),
    ).toBeVisible();

    // "批准发布" button should be visible
    expect(
      screen.getByRole('button', { name: /批\s*准\s*发\s*布/ }),
    ).toBeVisible();
  });

  it('hides approval buttons for a user without parameter:approve permission', () => {
    // M-03: user without parameter:approve permission cannot approve
    renderApprovalPanel({
      candidate: baseCandidate,
      currentUser: { ...baseUser, id: 'u-other-003', permissions: [] },
      parameterId: 'param-001',
    });

    // "查看完整来源" link should be visible
    expect(
      screen.getByText(/查\s*看\s*完\s*整\s*来\s*源/),
    ).toBeVisible();

    // "批准发布" button should NOT be in the document (no permission)
    expect(
      screen.queryByRole('button', { name: /批\s*准\s*发\s*布/ }),
    ).not.toBeInTheDocument();

    // Should show the no-permission hint
    expect(
      screen.getByText(/无参数审批权限/),
    ).toBeVisible();
  });

  it('hides approval buttons when candidate is not in pending status', () => {
    // M-03: candidate not in in_review status cannot be approved
    renderApprovalPanel({
      candidate: { ...baseCandidate, status: 'published' },
      currentUser: reviewerUser,
      parameterId: 'param-001',
    });

    // "批准发布" button should NOT be in the document (not pending)
    expect(
      screen.queryByRole('button', { name: /批\s*准\s*发\s*布/ }),
    ).not.toBeInTheDocument();
  });

  // ---- Bug 1 回归测试：查看完整来源深链 run_id 传递 ----

  it('navigates to /lab-ops with provenance_run_id when clicking 查看完整来源', () => {
    // Bug 1 修复：从 href="/provenance?run_id=xxx" 改为 useNavigate('/lab-ops', {search})
    // 验证点击"查看完整来源"按钮时，navigate 被调用且 search 包含 provenance_run_id
    renderApprovalPanel({
      candidate: baseCandidate, // derivation_run_id = 'run-001'
      currentUser: reviewerUser,
      parameterId: 'param-001',
    });

    const link = screen.getByText(/查\s*看\s*完\s*整\s*来\s*源/);
    fireEvent.click(link);

    expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/lab-ops',
      search: {
        tab: 'parameters',
        param: 'param-001',
        provenance_run_id: 'run-001',
      },
    });
  });

  it('navigates to /lab-ops without provenance_run_id when candidate has no derivation_run_id', () => {
    // 候选没有 derivation_run_id 时，search 不应包含 provenance_run_id
    renderApprovalPanel({
      candidate: { ...baseCandidate, derivation_run_id: null },
      currentUser: reviewerUser,
      parameterId: 'param-001',
    });

    const link = screen.getByText(/查\s*看\s*完\s*整\s*来\s*源/);
    fireEvent.click(link);

    expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/lab-ops',
      search: {
        tab: 'parameters',
        param: 'param-001',
      },
    });
  });
});
