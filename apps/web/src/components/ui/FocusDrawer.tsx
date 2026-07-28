/**
 * FocusDrawer — 焦点抽屉
 *
 * 透传所有 Ant Design Drawer 行为属性，仅标准化 title/body/footer 结构
 * 和可选 error 区域。业务页面继续拥有打开状态和表单逻辑。
 *
 * 契约保证：
 * - title 通过 Ant Design Drawer 的 title prop 渲染为 dialog 的可访问名称
 * - error 区域在 body 之前渲染，带 role="alert"
 * - footer 顺序由 Ant Design Drawer 的 footer prop 控制
 * - 关闭后焦点自动返回触发元素（Ant Design Drawer 默认行为）
 * - 关闭按钮带 aria-label（Ant Design Drawer 默认提供）
 */
import type { PropsWithChildren, ReactNode } from 'react';
import { Drawer } from 'antd';
import type { DrawerProps } from 'antd';

export type FocusDrawerProps = PropsWithChildren<
  Omit<DrawerProps, 'children'> & { error?: ReactNode }
>;

export function FocusDrawer({ error, children, ...drawerProps }: FocusDrawerProps): JSX.Element {
  return (
    <Drawer {...drawerProps}>
      {error ? (
        <div className="ocean-focus-error" role="alert">
          {error}
        </div>
      ) : null}
      <div className="ocean-focus-body">{children}</div>
    </Drawer>
  );
}
