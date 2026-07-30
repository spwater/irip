/**
 * FlowTrack — 流程轨迹
 *
 * 职责（设计文档第 8.1 节）：
 * - 流程、作业、版本或时间关系
 *
 * 禁止：不伪造节点或进度。
 *
 * 视觉规则：
 * - 使用 Timeline 承载
 * - 节点状态映射到状态语义颜色
 * - 失败节点显示原因、时间和可执行操作
 */
import type { CSSProperties, ReactNode } from 'react';
import { Timeline } from 'antd';
import { STATUS_SEMANTIC_COLOR, type StatusSemantic } from '@/theme/tokens';

export interface FlowTrackItem {
  /** 节点标签 */
  label: string;
  /** 节点状态语义 */
  status: StatusSemantic;
  /** 时间（可复制） */
  time?: string;
  /** 附加内容（如失败原因、可执行操作） */
  description?: ReactNode;
}

export interface FlowTrackProps {
  /** 轨迹节点列表 */
  items: FlowTrackItem[];
  /** 透传样式 */
  style?: CSSProperties;
  /** 自定义 className */
  className?: string;
}

/** 状态语义 → Timeline color */
const STATUS_TIMELINE_COLOR: Record<StatusSemantic, string> = {
  neutral: STATUS_SEMANTIC_COLOR.neutral,
  info: STATUS_SEMANTIC_COLOR.info,
  success: STATUS_SEMANTIC_COLOR.success,
  warning: STATUS_SEMANTIC_COLOR.warning,
  danger: STATUS_SEMANTIC_COLOR.danger,
  special: STATUS_SEMANTIC_COLOR.special,
};

/**
 * 流程轨迹组件。
 *
 * 基于 Ant Design Timeline，节点状态映射到统一状态语义。
 * 失败节点通过 description 展示原因、时间和可执行操作。
 */
export function FlowTrack({
  items,
  style,
  className,
}: FlowTrackProps): JSX.Element {
  return (
    <div
      className={`ocean-flow-track${className ? ` ${className}` : ''}`}
      style={style}
    >
      <Timeline
        items={items.map((item, idx) => ({
          key: `${item.label}-${idx}`,
          color: STATUS_TIMELINE_COLOR[item.status],
          children: (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 8,
                }}
              >
                <span
                  style={{
                    fontSize: 14,
                    fontWeight: 500,
                    color: 'var(--ocean-text-primary)',
                  }}
                >
                  {item.label}
                </span>
                {item.time ? (
                  <span
                    className="ocean-tabular-nums"
                    style={{
                      fontSize: 12,
                      color: 'var(--ocean-text-muted)',
                      fontFamily: 'var(--ocean-font-mono)',
                    }}
                  >
                    {item.time}
                  </span>
                ) : null}
              </div>
              {item.description ? (
                <div
                  style={{
                    fontSize: 13,
                    color: 'var(--ocean-text-secondary)',
                    lineHeight: 1.6,
                  }}
                >
                  {item.description}
                </div>
              ) : null}
            </div>
          ),
        }))}
      />
    </div>
  );
}

export default FlowTrack;
