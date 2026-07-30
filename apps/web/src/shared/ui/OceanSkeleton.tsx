/**
 * OceanSkeleton — 加载占位
 *
 * 职责（设计文档第 8.1 节、第 12.1 节）：
 * - 与目标布局同构的加载占位
 *
 * 禁止：不造成布局跳动。
 *
 * 视觉规则：
 * - 首次加载显示与最终布局同构的 OceanSkeleton，避免跳动
 */
import type { CSSProperties } from 'react';
import { Skeleton } from 'antd';

export type OceanSkeletonType = 'table' | 'card' | 'detail';

export interface OceanSkeletonProps {
  /** 占位类型 */
  type?: OceanSkeletonType;
  /** 透传样式 */
  style?: CSSProperties;
  /** 表格行数（仅 type=table 时生效） */
  rows?: number;
}

/**
 * 加载占位组件。
 *
 * 三种类型：
 * - table：表头 + 多行表格占位
 * - card：卡片占位
 * - detail：详情分区占位
 *
 * 与目标布局同构，避免首次加载时布局跳动。
 */
export function OceanSkeleton({
  type = 'table',
  style,
  rows = 6,
}: OceanSkeletonProps): JSX.Element {
  const wrapperStyle: CSSProperties = {
    background: 'var(--ocean-surface-strong)',
    border: '1px solid var(--ocean-border-subtle)',
    borderRadius: 6,
    padding: 16,
    ...style,
  };

  switch (type) {
    case 'table':
      return (
        <div className="ocean-skeleton ocean-skeleton--table" style={wrapperStyle}>
          {/* 表头占位 */}
          <Skeleton.Input active size="small" block style={{ height: 36, marginBottom: 12 }} />
          {/* 表格行占位 */}
          {Array.from({ length: rows }).map((_, idx) => (
            <Skeleton
              key={idx}
              active
              paragraph={{ rows: 1, width: '100%' }}
              title={false}
              style={{ marginBottom: 8 }}
            />
          ))}
        </div>
      );

    case 'card':
      return (
        <div className="ocean-skeleton ocean-skeleton--card" style={wrapperStyle}>
          <Skeleton active paragraph={{ rows: 4 }} />
        </div>
      );

    case 'detail':
      return (
        <div className="ocean-skeleton ocean-skeleton--detail" style={wrapperStyle}>
          {/* 标题占位 */}
          <Skeleton.Input active size="small" style={{ width: 180, marginBottom: 16 }} />
          {/* 描述列表占位 */}
          {Array.from({ length: 4 }).map((_, idx) => (
            <Skeleton
              key={idx}
              active
              paragraph={{ rows: 1, width: '60%' }}
              title={{ width: '30%' }}
              style={{ marginBottom: 12 }}
            />
          ))}
        </div>
      );

    default:
      return <div style={wrapperStyle} />;
  }
}

export default OceanSkeleton;
