import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { App as AntApp } from 'antd';
import {
  getErrorStatus,
  deriveErrorState,
  QueryStateDisplay,
} from './StateDisplay';

function renderWithApp(ui: React.ReactElement): void {
  render(<AntApp>{ui}</AntApp>);
}

describe('getErrorStatus', () => {
  it('returns status code from axios error with response', () => {
    const error = { response: { status: 404 } };
    expect(getErrorStatus(error)).toBe(404);
  });

  it('returns 0 for network error with request but no response', () => {
    const error = { request: {} };
    expect(getErrorStatus(error)).toBe(0);
  });

  it('returns undefined for generic error without response or request', () => {
    expect(getErrorStatus(new Error('oops'))).toBeUndefined();
  });

  it('returns undefined for null', () => {
    expect(getErrorStatus(null)).toBeUndefined();
  });

  it('returns undefined when response.status is not a number', () => {
    const error = { response: { status: 'bad' } };
    expect(getErrorStatus(error)).toBeUndefined();
  });
});

describe('deriveErrorState', () => {
  it('returns 登录已失效 for 401', () => {
    const result = deriveErrorState({ response: { status: 401 } });
    expect(result.state).toBe('error');
    expect(result.title).toBe('登录已失效');
    expect(result.description).toContain('会话已过期');
  });

  it('returns forbidden state for 403', () => {
    const result = deriveErrorState({ response: { status: 403 } });
    expect(result.state).toBe('forbidden');
    expect(result.title).toBe('无访问权限');
  });

  it('returns 资源不存在 for 404', () => {
    const result = deriveErrorState({ response: { status: 404 } });
    expect(result.title).toBe('资源不存在');
  });

  it('returns 数据冲突 for 409', () => {
    const result = deriveErrorState({ response: { status: 409 } });
    expect(result.title).toBe('数据冲突');
  });

  it('returns 网络连接失败 for status 0', () => {
    const result = deriveErrorState({ request: {} });
    expect(result.title).toBe('网络连接失败');
  });

  it('returns 服务暂时不可用 for 500', () => {
    const result = deriveErrorState({ response: { status: 500 } });
    expect(result.title).toBe('服务暂时不可用');
    expect(result.description).toContain('500');
  });

  it('returns 服务暂时不可用 for 503', () => {
    const result = deriveErrorState({ response: { status: 503 } });
    expect(result.title).toBe('服务暂时不可用');
  });

  it('returns generic 加载失败 for unknown error', () => {
    const result = deriveErrorState(new Error('unknown'));
    expect(result.title).toBe('加载失败');
  });
});

describe('QueryStateDisplay', () => {
  it('renders loading state when isLoading', () => {
    renderWithApp(
      <QueryStateDisplay isLoading={true} isError={false} error={null}>
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    expect(screen.queryByTestId('content')).not.toBeInTheDocument();
  });

  it('renders children when not loading, no error, not empty', () => {
    renderWithApp(
      <QueryStateDisplay isLoading={false} isError={false} error={null}>
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    expect(screen.getByTestId('content')).toBeInTheDocument();
  });

  it('renders error state when isError', () => {
    renderWithApp(
      <QueryStateDisplay isLoading={false} isError={true} error={{ response: { status: 403 } }}>
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    expect(screen.queryByTestId('content')).not.toBeInTheDocument();
    expect(screen.getByText('无访问权限')).toBeInTheDocument();
  });

  it('renders empty state when isEmpty', () => {
    renderWithApp(
      <QueryStateDisplay isLoading={false} isError={false} error={null} isEmpty={true} emptyText="暂无数据">
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    expect(screen.queryByTestId('content')).not.toBeInTheDocument();
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('renders default empty text when no emptyText provided', () => {
    renderWithApp(
      <QueryStateDisplay isLoading={false} isError={false} error={null} isEmpty={true}>
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('renders retry button when onRetry provided in error state', () => {
    const onRetry = vi.fn();
    renderWithApp(
      <QueryStateDisplay isLoading={false} isError={true} error={{ response: { status: 404 } }} onRetry={onRetry}>
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    // Retry button should be present
    const retryBtn = screen.getByRole('button', { name: /重\s*试/ });
    expect(retryBtn).toBeInTheDocument();
  });

  it('renders loading title when loadingTitle provided', () => {
    renderWithApp(
      <QueryStateDisplay isLoading={true} isError={false} error={null} loadingTitle="加载列表中…">
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    expect(screen.queryByTestId('content')).not.toBeInTheDocument();
    expect(document.querySelector('.ant-spin')).toBeInTheDocument();
  });

  it('renders default loading title', () => {
    renderWithApp(
      <QueryStateDisplay isLoading={true} isError={false} error={null}>
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    expect(screen.queryByTestId('content')).not.toBeInTheDocument();
    expect(document.querySelector('.ant-spin')).toBeInTheDocument();
  });

  it('priority: loading over error', () => {
    renderWithApp(
      <QueryStateDisplay isLoading={true} isError={true} error={{ response: { status: 404 } }}>
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    expect(screen.queryByTestId('content')).not.toBeInTheDocument();
    expect(document.querySelector('.ant-spin')).toBeInTheDocument();
    expect(screen.queryByText('资源不存在')).not.toBeInTheDocument();
  });

  it('priority: error over empty', () => {
    renderWithApp(
      <QueryStateDisplay isLoading={false} isError={true} error={{ response: { status: 404 } }} isEmpty={true}>
        <div data-testid="content">content</div>
      </QueryStateDisplay>,
    );
    expect(screen.getByText('资源不存在')).toBeInTheDocument();
  });
});
