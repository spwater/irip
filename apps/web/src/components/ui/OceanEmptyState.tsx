/**
 * OceanEmptyState — 空状态
 *
 * 职责（设计文档第 8.1 节、第 12.1 节）：
 * - 解释空数据原因和下一步
 *
 * 禁止：不使用纯装饰文案。
 *
 * 视觉规则：
 * - 空数据解释原因，提供可执行下一步
 * - 无权限时不得显示为空数据（应使用 FeedbackState forbidden）
 */
import type { CSSProperties, ReactNode } from 'react';
import { Empty } from 'antd';

export interface OceanEmptyStateProps {
  /** 空状态标题/原因说明 */
  title: string;
  /** 详细说明 */
  description?: string;
  /** 可执行下一步操作 */
  action?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
}

/**
 * 空状态组件。
 *
 * 解释空数据原因并提供可执行下一步；不使用纯装饰文案。
 */
export function OceanEmptyState({
  title,
  description,
  action,
  style,
}: OceanEmptyStateProps): JSX.Element {
  return (
    <div
      className="ocean-empty-state"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '40px 24px',
        ...style,
      }}
    >
      <Empty
        description={
          <div style={{ maxWidth: 360, textAlign: 'center' }}>
            <div
              style={{
                fontSize: 14,
                fontWeight: 500,
                color: 'var(--ocean-text-primary)',
                marginBottom: 4,
              }}
            >
              {title}
            </div>
            {description ? (
              <div style={{ fontSize: 13, color: 'var(--ocean-text-secondary)' }}>
                {description}
              </div>
            ) : null}
          </div>
        }
      >
        {action ? (
          <div style={{ marginTop: 16, display: 'flex', justifyContent: 'center' }}>
            {action}
          </div>
        ) : null}
      </Empty>
    </div>
  );
}

export default OceanEmptyState;
