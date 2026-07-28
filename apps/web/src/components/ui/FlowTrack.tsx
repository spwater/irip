/**
 * FlowTrack — 流程轨道
 *
 * 展示已计算好的流程步骤及其状态语调和进度。
 * 组件不推断业务状态，仅接收业务层计算后的节点和进度值。
 */
import type { ReactNode } from 'react';
import { statusTone, type StatusTone } from '@/theme/tokens';

export type FlowTrackItem = {
  key: string;
  label: string;
  tone: StatusTone;
  detail?: ReactNode;
  progress?: number;
};

export type FlowTrackProps = {
  items: FlowTrackItem[];
  activeKey?: string;
};

export function FlowTrack({ items, activeKey }: FlowTrackProps): JSX.Element {
  return (
    <ol className="ocean-flow-track" role="list">
      {items.map((item) => {
        const config = statusTone[item.tone];
        const isActive = item.key === activeKey;
        const itemClass = `ocean-flow-track__item${isActive ? ' ocean-flow-track__item--active' : ''}`;
        return (
          <li
            key={item.key}
            className={itemClass}
            data-tone={item.tone}
            data-marker={config.marker}
          >
            <span
              className="ocean-flow-track__marker"
              aria-hidden="true"
              style={{ color: config.color }}
            />
            <div className="ocean-flow-track__content">
              <div className="ocean-flow-track__label">{item.label}</div>
              {item.detail ? <div className="ocean-flow-track__detail">{item.detail}</div> : null}
              {typeof item.progress === 'number' ? (
                <div className="ocean-flow-track__progress">
                  <div
                    className="ocean-flow-track__progress-bar"
                    style={{ width: `${item.progress}%` }}
                  />
                </div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
