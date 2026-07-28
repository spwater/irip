/**
 * FeedbackState — 加载/空/错误/权限/部分失败状态
 *
 * 职责（设计文档第 8.1 节、第 12 节）：
 * - loading / empty / error / forbidden / partial
 *
 * 禁止：错误时不清空已有数据。
 *
 * 视觉规则：
 * - 首次加载显示与最终布局同构的 OceanSkeleton
 * - 空数据解释原因，提供可执行下一步
 * - 查询失败显示可行动摘要、重试按钮和必要错误标识
 * - 权限不足明确显示无权限
 * - 部分失败其他有效数据继续显示，并展示“部分数据未加载”
 */
import type { CSSProperties, ReactNode } from 'react';
import { Alert, Button, Empty, Result, Spin } from 'antd';

export type FeedbackStateType =
  | 'loading'
  | 'empty'
  | 'error'
  | 'forbidden'
  | 'partial';

export interface FeedbackStateProps {
  /** 状态类型 */
  state: FeedbackStateType;
  /** 标题 */
  title?: string;
  /** 说明 */
  description?: string;
  /** 可执行操作（如重试按钮） */
  action?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
}

/**
 * 反馈状态组件。
 *
 * - loading：居中 Spin
 * - empty：Empty + 可选操作
 * - error：Result error + 可选操作
 * - forbidden：Result 403 + 可选操作
 * - partial：Alert warning，提示“部分数据未加载”
 *
 * 注意：错误时不清空已有数据，调用方决定何时渲染此组件。
 */
export function FeedbackState({
  state,
  title,
  description,
  action,
  style,
}: FeedbackStateProps): JSX.Element {
  const containerStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
    ...style,
  };

  switch (state) {
    case 'loading':
      return (
        <div className="ocean-feedback-state ocean-feedback-state--loading" style={containerStyle}>
          <Spin tip={title ?? '加载中…'} size="large" />
        </div>
      );

    case 'empty':
      return (
        <div className="ocean-feedback-state ocean-feedback-state--empty" style={containerStyle}>
          <Empty
            description={description ?? title ?? '暂无数据'}
            style={{ maxWidth: 360 }}
          >
            {action ? <div style={{ marginTop: 16 }}>{action}</div> : null}
          </Empty>
        </div>
      );

    case 'error':
      return (
        <div className="ocean-feedback-state ocean-feedback-state--error" style={containerStyle}>
          <Result
            status="error"
            title={title ?? '加载失败'}
            subTitle={description ?? '请稍后重试，或联系管理员。'}
            extra={action}
          />
        </div>
      );

    case 'forbidden':
      return (
        <div className="ocean-feedback-state ocean-feedback-state--forbidden" style={containerStyle}>
          <Result
            status="403"
            title={title ?? '无权限'}
            subTitle={description ?? '当前账号无权访问此内容，请切换账号或返回。'}
            extra={action}
          />
        </div>
      );

    case 'partial':
      return (
        <div className="ocean-feedback-state ocean-feedback-state--partial" style={{ ...style }}>
          <Alert
            type="warning"
            showIcon
            message={title ?? '部分数据未加载'}
            description={description ?? '当前仅显示已成功获取的数据，部分内容可能缺失。'}
            action={action ? <div style={{ marginTop: 8 }}>{action}</div> : undefined}
          />
        </div>
      );

    default:
      return <div style={containerStyle} />;
  }
}

/**
 * 默认重试按钮工厂（便于调用方快速提供 action）。
 */
export function RetryAction({ onRetry }: { onRetry: () => void }): JSX.Element {
  return (
    <Button type="primary" onClick={onRetry}>
      重试
    </Button>
  );
}

export default FeedbackState;
