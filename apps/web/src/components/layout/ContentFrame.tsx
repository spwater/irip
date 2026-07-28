/**
 * ContentFrame — 内容宽度容器
 *
 * 'standard' = 1280px 最大宽度（详情/表单页）
 * 'wide'     = 1680px 最大宽度（看板/表格页）
 * 两侧 72px 网格间距，居中对齐。
 */
import type { PropsWithChildren } from 'react';

export function ContentFrame({
  children,
  width = 'standard',
}: PropsWithChildren<{ width?: 'standard' | 'wide' }>): JSX.Element {
  return (
    <div className="ocean-content-frame" data-width={width}>
      {children}
    </div>
  );
}
