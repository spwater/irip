/**
 * StatusMark — 状态标记
 *
 * 非颜色依赖的状态指示器：通过 data-tone + data-marker 属性
 * 和装饰标记形状（solid/cross/diamond/ring/outline）传达语义，
 * 同时渲染可见标签文本。装饰标记带 aria-hidden="true"。
 */
import { statusTone, type StatusTone } from '@/theme/tokens';

export type StatusMarkProps = {
  /** 状态语调 */
  tone: StatusTone;
  /** 可见标签文本 */
  label: string;
  /** 补充详情 */
  detail?: string;
};

export function StatusMark({ tone, label, detail }: StatusMarkProps): JSX.Element {
  const config = statusTone[tone];
  return (
    <span className="ocean-status-mark" data-tone={tone} data-marker={config.marker}>
      <span
        className="ocean-status-mark__marker"
        aria-hidden="true"
        style={{ color: config.color }}
      />
      <span className="ocean-status-mark__label">{label}</span>
      {detail ? <span className="ocean-status-mark__detail">{detail}</span> : null}
    </span>
  );
}
