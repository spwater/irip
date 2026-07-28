/**
 * FocusModal — 焦点模态
 *
 * 透传所有 Ant Design Modal 行为属性，仅标准化 title/body/footer 结构
 * 和可选 error 区域。业务页面继续拥有打开状态和表单逻辑。
 */
import type { PropsWithChildren, ReactNode } from 'react';
import { Modal } from 'antd';
import type { ModalProps } from 'antd';

export type FocusModalProps = PropsWithChildren<
  Omit<ModalProps, 'children'> & { error?: ReactNode }
>;

export function FocusModal({ error, children, ...modalProps }: FocusModalProps): JSX.Element {
  return (
    <Modal {...modalProps}>
      {error ? (
        <div className="ocean-focus-error" role="alert">
          {error}
        </div>
      ) : null}
      <div className="ocean-focus-body">{children}</div>
    </Modal>
  );
}
