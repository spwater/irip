/**
 * ConstructionTrack — 实验室建设链路轨道
 *
 * 展示实验室建设三阶段链路：组织机构 → 设备仪器 → 实验对象。
 * 当前活跃阶段高亮显示，辅助用户理解跨 Tab 预填链路的方向。
 * 组件不持有业务状态，仅接收 activeKey 进行视觉标记。
 */

/** 链路阶段定义 */
const STAGES = [
  { key: 'departments', index: '01', label: '组织机构' },
  { key: 'equipment', index: '02', label: '设备仪器' },
  { key: 'exp-objects', index: '03', label: '实验对象' },
] as const;

export type ConstructionTrackProps = {
  /** 当前活跃阶段 key */
  activeKey: string;
};

/**
 * 渲染实验室建设三阶段链路。
 *
 * @param activeKey - 当前激活的阶段 key（departments / equipment / exp-objects）
 */
export function ConstructionTrack({ activeKey }: ConstructionTrackProps): JSX.Element {
  return (
    <ol className="ocean-construction-track" aria-label="实验室建设链路">
      {STAGES.map((stage) => (
        <li key={stage.key} data-active={stage.key === activeKey}>
          <span aria-hidden="true">{stage.index}</span>
          {stage.label}
        </li>
      ))}
    </ol>
  );
}
