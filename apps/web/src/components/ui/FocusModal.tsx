/**
 * FocusModal — 焦点模态
 *
 * 透传所有 Ant Design Modal 行为属性，仅标准化 title/body error/footer 结构
 * 和可选 error 区域。业务页面继续拥有打开状态和表单逻辑。
 *
 * 契约保证：
 * - title 通过 Ant Design Modal 的 title prop 渲染为 dialog 的可访问名称
 * - error 区域在 body 之前渲染，带 role="alert"
 * - footer 顺序由 Ant Design Modal 的 footer/okText/cancelText 控制
 * - 关闭后焦点自动返回触发元素（focusTriggerAfterClose 默认 true）
 */
import type { PropsWithChildren, ReactNode } from 'react';
import { Modal } from 'antd';
import type { ModalProps } from 'antd';

export type FocusModalProps = PropsWithChildren<
  Omit<ModalProps, 'children'> & { error?: ReactNode }
>;

export function FocusModal({
  error,
  children,
  focusTriggerAfterClose = true,
  ...modalProps
}: FocusModalProps): JSX.Element {
  return (
    <Modal {...modalProps} focusTriggerAfterClose={focusTriggerAfterClose}>
      {error ? (
        <div className="ocean-focus-error" role="alert">
          {error}
        </div>
      ) : null}
      <div className="ocean-focus-body">{children}</div>
    </Modal>
  );
}
