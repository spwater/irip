import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import type { CurrentUser, ParameterCandidate } from '@/api/client';
import { ApprovalPanel } from '@/parameters/ApprovalPanel';

// vi.mock 必须在 import 之前（vitest 会自动提升）
vi.mock('@/api/client', () => ({
  apiApproveCandidate: vi.fn(),
  apiRejectCandidate: vi.fn(),
  extractApiError: (err: unknown) =>
    err instanceof Error ? err.message : '操作失败',
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
  it('shows provenance but hides approval for the submitter', () => {
    // Current user IS the submitter — self_approval_forbidden
    renderApprovalPanel({
      candidate: baseCandidate,
      currentUser: baseUser,
      parameterId: 'param-001',
    });

    // "查看完整来源" link should be visible
    expect(
      screen.getByText(/查\s*看\s*完\s*整\s*来\s*源/),
    ).toBeVisible();

    // "批准发布" button should NOT be in the document
    expect(
      screen.queryByRole('button', { name: /批\s*准\s*发\s*布/ }),
    ).not.toBeInTheDocument();
  });

  it('shows both provenance and approval for a different reviewer', () => {
    // Current user is NOT the submitter — can approve
    renderApprovalPanel({
      candidate: baseCandidate,
      currentUser: { ...baseUser, id: 'u-reviewer-002' },
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
});
