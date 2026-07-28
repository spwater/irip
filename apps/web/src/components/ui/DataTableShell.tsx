/**
 * DataTableShell — 数据表壳
 *
 * 统一表格页面的标题/描述/工具栏 + 表格主体的布局结构。
 * 业务页面继续拥有列定义、分页和行操作。
 */
import type { PropsWithChildren, ReactNode } from 'react';

export type DataTableShellProps = PropsWithChildren<{
  title?: ReactNode;
  description?: ReactNode;
  toolbar?: ReactNode;
}>;

export function DataTableShell({
  title,
  description,
  toolbar,
  children,
}: DataTableShellProps): JSX.Element {
  return (
    <div className="ocean-data-table-shell">
      {title || description || toolbar ? (
        <div className="ocean-data-table-shell__header">
          <div className="ocean-data-table-shell__heading">
            {title ? <div className="ocean-data-table-shell__title">{title}</div> : null}
            {description ? (
              <div className="ocean-data-table-shell__description">{description}</div>
            ) : null}
          </div>
          {toolbar ? <div className="ocean-data-table-shell__toolbar">{toolbar}</div> : null}
        </div>
      ) : null}
      <div className="ocean-data-table-shell__body">{children}</div>
    </div>
  );
}
