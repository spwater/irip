/**
 * 共用 UI 组件统一导出入口
 *
 * 设计文档第 8.1 节定义的 13 个组件，保持轻量，
 * 业务页面仍直接定义 columns、Form values、query 和 mutation。
 */
export { PageIntro } from './PageIntro';
export type { PageIntroProps } from './PageIntro';

export { DataHero } from './DataHero';
export type { DataHeroProps } from './DataHero';

export { MetricStrip } from './MetricStrip';
export type { MetricStripProps, MetricItem } from './MetricStrip';

export { OceanPanel } from './OceanPanel';
export type { OceanPanelProps, OceanPanelVariant } from './OceanPanel';

export { ActionBar } from './ActionBar';
export type { ActionBarProps } from './ActionBar';

export { DataTableShell } from './DataTableShell';
export type { DataTableShellProps } from './DataTableShell';

export { StatusMark } from './StatusMark';
export type { StatusMarkProps, StatusMarkShape } from './StatusMark';

export { FeedbackState, RetryAction } from './FeedbackState';
export type { FeedbackStateProps, FeedbackStateType } from './FeedbackState';

export { DetailSection } from './DetailSection';
export type { DetailSectionProps } from './DetailSection';

export { FlowTrack } from './FlowTrack';
export type { FlowTrackProps, FlowTrackItem } from './FlowTrack';

export { FocusDrawer } from './FocusDrawer';
export type { FocusDrawerProps, FocusDrawerWidth } from './FocusDrawer';

export { FocusModal } from './FocusModal';
export type { FocusModalProps } from './FocusModal';

export { OceanEmptyState } from './OceanEmptyState';
export type { OceanEmptyStateProps } from './OceanEmptyState';

export { OceanSkeleton } from './OceanSkeleton';
export type { OceanSkeletonProps, OceanSkeletonType } from './OceanSkeleton';

export { GradLine } from './GradLine';
export type { GradLineProps } from './GradLine';

export { EcgLine } from './EcgLine';
export type { EcgLineProps } from './EcgLine';
