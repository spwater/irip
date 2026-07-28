/**
 * StatusMark — 状态视觉字典
 *
 * 职责（设计文档第 8.1 节、第 8.2 节）：
 * - 状态色、文字和点/线标记
 *
 * 禁止：不只依赖颜色（必须同时有文字或图形）。
 *
 * 状态语义字典（第 8.2 节）：
 * | 语义 | 典型状态 | 视觉 |
 * | neutral | draft、inactive、deprecated | 蓝灰文字 + 空心标记 |
 * | info | running、processing、in_review | 信息蓝 + 实心点/进度 |
 * | success | active、published、validated、succeeded | 青绿 + 实心标记 |
 * | warning | pending、retryable、部分完成 | 琥珀 + 菱形/提示图形 |
 * | danger | failed、rejected、unhealthy | 珊瑚红 + 错误图形 |
 * | special | AI、候选工具、特殊模型阶段 | 克制紫色，仅必要时使用 |
 */
import type { CSSProperties } from 'react';
import { STATUS_SEMANTIC_COLOR, type StatusSemantic } from '@/theme/tokens';

export type StatusMarkShape = 'dot' | 'ring' | 'diamond' | 'bar';

export interface StatusMarkProps {
  /** 状态语义 */
  semantic: StatusSemantic;
  /** 状态文字（中文标签） */
  label: string;
  /** 标记形状，默认 dot */
  shape?: StatusMarkShape;
  /** 透传样式 */
  style?: CSSProperties;
}

/** 形状 → 渲染样式映射 */
function renderMark(shape: StatusMarkShape, color: string): JSX.Element {
  const base: CSSProperties = {
    display: 'inline-block',
    flex: '0 0 auto',
  };
  switch (shape) {
    case 'dot':
      return (
        <span
          style={{
            ...base,
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: color,
          }}
        />
      );
    case 'ring':
      return (
        <span
          style={{
            ...base,
            width: 8,
            height: 8,
            borderRadius: '50%',
            border: `1.5px solid ${color}`,
            background: 'transparent',
          }}
        />
      );
    case 'diamond':
      return (
        <span
          style={{
            ...base,
            width: 8,
            height: 8,
            background: color,
            transform: 'rotate(45deg)',
            borderRadius: 1,
          }}
        />
      );
    case 'bar':
      return (
        <span
          style={{
            ...base,
            width: 3,
            height: 12,
            borderRadius: 1.5,
            background: color,
          }}
        />
      );
    default:
      return <span />;
  }
}

/** 语义 → 默认形状 */
const DEFAULT_SHAPE: Record<StatusSemantic, StatusMarkShape> = {
  neutral: 'ring',
  info: 'dot',
  success: 'dot',
  warning: 'diamond',
  danger: 'dot',
  special: 'diamond',
};

/**
 * 状态视觉标记。
 *
 * 同时表达颜色 + 形状 + 文字，不只依赖颜色。
 */
export function StatusMark({
  semantic,
  label,
  shape,
  style,
}: StatusMarkProps): JSX.Element {
  const color = STATUS_SEMANTIC_COLOR[semantic];
  const finalShape = shape ?? DEFAULT_SHAPE[semantic];

  return (
    <span
      className={`ocean-status-mark ocean-status-mark--${semantic}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 8px',
        borderRadius: 2,
        background:
          semantic === 'neutral'
            ? 'rgba(142, 191, 208, 0.16)'
            : `${color}1a`,
        fontSize: 12,
        lineHeight: 1.5,
        color,
        fontWeight: 500,
        ...style,
      }}
    >
      {renderMark(finalShape, color)}
      <span>{label}</span>
    </span>
  );
}

export default StatusMark;
