/**
 * StateDisplay - 统一的请求状态展示组件 (M-06)
 *
 * 目的：解决多数前端请求失败时显示空列表或永久 loading 的问题。
 *
 * 职责：
 * - loading：首次加载显示骨架/Spin
 * - error：请求失败时根据 HTTP 状态码区分 401/403/404/409/5xx，提供可重试操作
 * - empty：数据为空时解释原因
 * - 成功：渲染 children
 *
 * 使用方式：
 *   <QueryStateDisplay
 *     isLoading={isLoading}
 *     isError={isError}
 *     error={error}
 *     isEmpty={!isLoading && !isError && items.length === 0}
 *     onRetry={() => refetch()}
 *   >
 *     <Table ... />
 *   </QueryStateDisplay>
 */
import type { CSSProperties, ReactNode } from 'react';
import { FeedbackState, RetryAction } from '@/components/ui';

/**
 * 从 Axios/未知错误中提取 HTTP 状态码。
 *
 * 后端响应拦截器在 401 时会自动刷新并重试，因此到达组件层的 401
 * 通常意味着 refresh 也失败（会话已清）。其他状态码按字面语义处理。
 */
export function getErrorStatus(error: unknown): number | undefined {
  if (error && typeof error === 'object' && 'response' in error) {
    const response = (error as { response?: { status?: number } }).response;
    if (typeof response?.status === 'number') {
      return response.status;
    }
  }
  // 网络错误（无 response）返回 0 表示无状态码
  if (error && typeof error === 'object' && 'request' in error) {
    return 0;
  }
  return undefined;
}

/** 根据错误推导出的展示状态 */
export type ErrorDisplayState = {
  state: 'error' | 'forbidden';
  title: string;
  description: string;
};

/**
 * 根据错误对象推导展示状态（标题、说明、语义）。
 *
 * - 401：会话已失效（refresh 也失败），提示重新登录
 * - 403：无权限
 * - 404：资源不存在
 * - 409：并发冲突，数据已被他人修改
 * - 5xx：服务端异常
 * - 0：网络不可达
 * - 其他：通用失败
 */
export function deriveErrorState(error: unknown): ErrorDisplayState {
  const status = getErrorStatus(error);
  switch (status) {
    case 401:
      return {
        state: 'error',
        title: '登录已失效',
        description: '会话已过期或已在其他位置登录，请刷新页面重新登录。',
      };
    case 403:
      return {
        state: 'forbidden',
        title: '无访问权限',
        description: '当前账号无权访问此内容，请切换账号或联系管理员。',
      };
    case 404:
      return {
        state: 'error',
        title: '资源不存在',
        description: '请求的资源未找到，可能已被删除或链接有误。',
      };
    case 409:
      return {
        state: 'error',
        title: '数据冲突',
        description: '数据已被他人修改，请刷新后重试。',
      };
    case 0:
      return {
        state: 'error',
        title: '网络连接失败',
        description: '无法连接到服务器，请检查网络后重试。',
      };
    default:
      if (status !== undefined && status >= 500) {
        return {
          state: 'error',
          title: '服务暂时不可用',
          description: `服务器返回错误（${status}），请稍后重试。`,
        };
      }
      return {
        state: 'error',
        title: '加载失败',
        description: '请稍后重试，或联系管理员。',
      };
  }
}

export interface QueryStateDisplayProps {
  /** 是否首次加载（无缓存数据时的 loading） */
  isLoading: boolean;
  /** 查询是否出错 */
  isError: boolean;
  /** 错误对象（来自 react-query 的 error） */
  error: unknown;
  /** 数据是否为空（仅在非 loading、非 error 时生效） */
  isEmpty?: boolean;
  /** 空状态说明文本 */
  emptyText?: string;
  /** 重试回调（用于 error/empty 状态的操作按钮） */
  onRetry?: () => void;
  /** 加载中文案 */
  loadingTitle?: string;
  /** 透传样式 */
  style?: CSSProperties;
  /** 成功时渲染的内容 */
  children?: ReactNode;
}

/**
 * 统一查询状态展示组件。
 *
 * 优先级：loading > error > empty > children
 *
 * 注意：错误时不清空已有数据由调用方决定。本组件用于"整页/整块"
 * 数据未就绪的场景；若需保留已有数据并叠加错误提示，请使用
 * FeedbackState 的 partial 状态。
 */
export function QueryStateDisplay({
  isLoading,
  isError,
  error,
  isEmpty = false,
  emptyText,
  onRetry,
  loadingTitle,
  style,
  children,
}: QueryStateDisplayProps): JSX.Element | null {
  // 首次加载
  if (isLoading) {
    return (
      <FeedbackState
        state="loading"
        title={loadingTitle ?? '加载中…'}
        style={style}
      />
    );
  }

  // 请求失败
  if (isError) {
    const { state, title, description } = deriveErrorState(error);
    const action = onRetry ? <RetryAction onRetry={onRetry} /> : undefined;
    return (
      <FeedbackState
        state={state}
        title={title}
        description={description}
        action={action}
        style={style}
      />
    );
  }

  // 数据为空
  if (isEmpty) {
    const action = onRetry ? <RetryAction onRetry={onRetry} /> : undefined;
    return (
      <FeedbackState
        state="empty"
        description={emptyText ?? '暂无数据'}
        action={action}
        style={style}
      />
    );
  }

  // 成功
  return <>{children}</>;
}

export default QueryStateDisplay;
