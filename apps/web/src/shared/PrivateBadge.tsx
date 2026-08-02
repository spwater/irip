/**
 * 私有数据徽标组件
 *
 * 当 visibility_scope === 'private' 时渲染红色 Tag + 🔒 图标。
 * 用于列表页和详情页标识私有数据。
 */
import { Tag } from 'antd';
import { LockOutlined } from '@ant-design/icons';

/** PrivateBadge 组件 Props */
export type PrivateBadgeProps = {
  /** 可见范围：private 时渲染徽标 */
  visibility_scope: 'private' | 'tree' | 'explicit' | 'all';
  /** 是否带边距 */
  style?: React.CSSProperties;
};

/**
 * 私有数据徽标组件
 *
 * visibility_scope === 'private' 时渲染红色 Tag + 🔒 图标 + "私有" 文字。
 * 其他值不渲染任何内容。
 */
export function PrivateBadge({ visibility_scope, style }: PrivateBadgeProps): JSX.Element | null {
  if (visibility_scope !== 'private') return null;

  return (
    <Tag
      color="red"
      icon={<LockOutlined />}
      style={{
        margin: 0,
        padding: '2px 8px',
        borderRadius: 4,
        fontSize: 12,
        ...style,
      }}
    >
      私有
    </Tag>
  );
}
