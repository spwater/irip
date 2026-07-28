/**
 * FocusModal — 弹窗外观
 *
 * 职责（设计文档第 8.1 节、第 8.4 节）：
 * - 短表单、确认和单一任务
 *
 * 禁止：不承载长详情页面（长任务应转为 Drawer 或页面）。
 *
 * 视觉规则：
 * - 使用 strong surface 承载
 * - 底部操作顺序：次要操作在左/前，主要操作在右/后
 * - 危险操作与普通主按钮分离
 */
import type { CSSProperties, ReactNode } from 'react';
import { Modal } from 'antd';

export interface FocusModalProps {
  /** 是否打开 */
  open: boolean;
  /** 弹窗标题 */
  title: string;
  /** 确认回调（主操作） */
  onOk?: () => void;
  /** 取消回调 */
  onCancel?: () => void;
  /** 确认按钮文字 */
  okText?: string;
  /** 取消按钮文字 */
  cancelText?: string;
  /** 是否为危险操作 */
  danger?: boolean;
  /** 确认按钮 loading */
  confirmLoading?: boolean;
  /** 弹窗内容 */
  children?: ReactNode;
  /** 弹窗宽度 */
  width?: number | string;
  /** 透传样式 */
  style?: CSSProperties;
}

/**
 * 短表单/确认弹窗外观。
 *
 * 基于 Ant Design Modal。
 */
export function FocusModal({
  open,
  title,
  onOk,
  onCancel,
  okText = '确定',
  cancelText = '取消',
  danger = false,
  confirmLoading = false,
  children,
  width = 480,
  style,
}: FocusModalProps): JSX.Element {
  return (
    <Modal
      open={open}
      title={title}
      onOk={onOk}
      onCancel={onCancel}
      okText={okText}
      cancelText={cancelText}
      okButtonProps={{ danger, loading: confirmLoading }}
      width={width}
      styles={{
        header: {
          borderBottom: '1px solid var(--ocean-border-subtle)',
          marginBottom: 16,
        },
        body: {
          padding: '4px 0 0',
        },
        footer: {
          borderTop: '1px solid var(--ocean-border-subtle)',
          marginTop: 16,
          paddingTop: 12,
        },
      }}
      style={style}
      maskClosable={!danger}
    >
      {children}
    </Modal>
  );
}

export default FocusModal;
