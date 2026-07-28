/**
 * FocusDrawer — 焦点抽屉
 *
 * 透传所有 Ant Design Drawer 行为属性，仅标准化 title/body/footer 结构
 * 和可选 error 区域。业务页面继续拥有打开状态和表单逻辑。
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
