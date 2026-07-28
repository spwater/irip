/**
 * FeedbackState — 反馈状态（判别联合）
 *
 * 统一处理 loading / empty / forbidden / error / partial 五种反馈状态。
 * loading 使用布局稳定的 Skeleton 行；error 渲染"重试"按钮；
 * forbidden 不复用 empty 的图标或文案；partial 使用警告语义。
 */
import type { ReactNode } from 'react';
import { Button, Empty, Result, Skeleton } from 'antd';

export type FeedbackStateProps =
  | { kind: 'loading'; title?: string; rows?: number }
  | { kind: 'empty' | 'forbidden'; title: string; description?: string; action?: ReactNode }
  | { kind: 'error'; title: string; description?: string; onRetry: () => void }
  | { kind: 'partial'; title: string; description?: string; onRetry?: () => void };

export function FeedbackState(props: FeedbackStateProps): JSX.Element {
  if (props.kind === 'loading') {
    const rows = props.rows ?? 3;
    return (
      <div className="ocean-feedback ocean-feedback--loading" role="status" aria-live="polite">
        {props.title ? <div className="ocean-feedback__title">{props.title}</div> : null}
        <Skeleton active paragraph={{ rows }} title={false} />
      </div>
    );
  }

  if (props.kind === 'empty') {
    return (
      <div className="ocean-feedback ocean-feedback--empty">
        <Empty description={props.title}>
          {props.description ? (
            <div className="ocean-feedback__description">{props.description}</div>
          ) : null}
          {props.action}
        </Empty>
      </div>
    );
  }

  if (props.kind === 'forbidden') {
    return (
      <div className="ocean-feedback ocean-feedback--forbidden" role="alert">
        <Result
          status="403"
          title={props.title}
          subTitle={props.description}
          extra={props.action}
        />
      </div>
    );
  }

  if (props.kind === 'error') {
    return (
      <div className="ocean-feedback ocean-feedback--error" role="alert">
        <Result
          status="error"
          title={props.title}
          subTitle={props.description}
          extra={
            <Button type="primary" onClick={props.onRetry}>
              重试
            </Button>
          }
        />
      </div>
    );
  }

  // kind === 'partial' — 显式检查以收窄判别联合类型
  if (props.kind === 'partial') {
    return (
      <div className="ocean-feedback ocean-feedback--partial" role="status" aria-live="polite">
        <Result
          status="warning"
          title={props.title}
          subTitle={props.description}
          extra={props.onRetry ? <Button onClick={props.onRetry}>重试</Button> : null}
        />
      </div>
    );
  }

  // 防御性兜底 — 所有判别联合成员已在上方处理
  return <div className="ocean-feedback" />;
}
