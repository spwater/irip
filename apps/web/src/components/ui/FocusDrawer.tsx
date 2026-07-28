/**
 * FocusDrawer — 抽屉外观
 *
 * 职责（设计文档第 8.1 节、第 8.4 节）：
 * - 详情、版本、编辑抽屉外观
 *
 * 禁止：不替代业务表单状态。
 *
 * 视觉规则：
 * - 宽度按内容分为 480 / 640 / 800px 三档
 * - 使用 strong surface 承载
 * - 底部操作顺序：次要操作在左/前，主要操作在右/后
 */
import type { CSSProperties, ReactNode } from 'react';
import { Drawer } from 'antd';

export type FocusDrawerWidth = 480 | 640 | 800;

export interface FocusDrawerProps {
  /** 是否打开 */
  open: boolean;
  /** 抽屉标题 */
  title: string;
  /** 宽度档位，默认 480 */
  width?: FocusDrawerWidth;
  /** 关闭回调 */
  onClose: () => void;
  /** 抽屉内容 */
  children?: ReactNode;
  /** 底部操作区（次要在左，主要在右） */
  footer?: ReactNode;
  /** 透传样式 */
  style?: CSSProperties;
}

/**
 * 详情/编辑抽屉外观。
 *
 * 基于 Ant Design Drawer，宽度按 480/640/800 三档。
 */
export function FocusDrawer({
  open,
  title,
  width = 480,
  onClose,
  children,
  footer,
  style,
}: FocusDrawerProps): JSX.Element {
  return (
    <Drawer
      title={title}
      open={open}
      onClose={onClose}
      width={width}
      placement="right"
      styles={{
        header: {
          borderBottom: '1px solid var(--ocean-border-subtle)',
          padding: '16px 24px',
        },
        body: {
          padding: 20,
        },
        footer: {
          borderTop: '1px solid var(--ocean-border-subtle)',
          padding: '12px 24px',
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 8,
        },
      }}
      style={style}
      footer={footer}
    >
      {children}
    </Drawer>
  );
}

export default FocusDrawer;
