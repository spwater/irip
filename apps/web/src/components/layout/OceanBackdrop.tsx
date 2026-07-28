/**
 * OceanBackdrop — 全局视觉舞台
 *
 * 渲染 Polar Mist 三色画布背景和装饰大气层（aria-hidden），
 * 内容层位于装饰之上。所有页面共享此舞台。
 */
import type { PropsWithChildren } from 'react';

export function OceanBackdrop({ children }: PropsWithChildren): JSX.Element {
  return (
    <div className="ocean-backdrop">
      <div className="ocean-atmosphere" data-testid="ocean-atmosphere" aria-hidden="true" />
      <div className="ocean-content-layer">{children}</div>
    </div>
  );
}
