/**
 * AI 助手 feature 模块统一导出。
 *
 * 橱窗相关组件由各自文件独立导出，此处集中 re-export 方便外部引用。
 */
export { AssistantPage } from '@/features/assistant/AssistantPage';
export { MessageThread } from '@/features/assistant/MessageThread';
export { ShowcasePanel } from '@/features/assistant/ShowcasePanel';
export { ShowcaseCard } from '@/features/assistant/ShowcaseCard';
export { BlockWrapper } from '@/features/assistant/BlockWrapper';
export { PlotlyBlock } from '@/features/assistant/PlotlyBlock';
export { ConversationSearch } from '@/features/assistant/ConversationSearch';
export { SummaryModal } from '@/features/assistant/SummaryModal';
