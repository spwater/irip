/**
 * ConclusionBarPanel — 右栏 Tab 容器（结论栏 / 结论库）。
 *
 * Ant Design Tabs：
 * - Tab1 "结论栏"：ConclusionBar（新）
 * - Tab2 "结论库"：ConclusionLibrary + SynthesisComposer（现有）
 *
 * finalize 成功后自动切换到结论库 Tab，并刷新 conclusions 列表。
 */
import { useState } from 'react';
import { Tabs, Typography } from 'antd';
import type { ConclusionRef } from '@/api/researchTimeline';
import { ConclusionBar } from './ConclusionBar';
import { ConclusionLibrary } from './ConclusionLibrary';
import { SynthesisComposer } from './SynthesisComposer';

const { Text } = Typography;

type TabKey = 'bar' | 'library';

interface Props {
  workspaceId: string;
  /** 结论库列表 */
  conclusions: ConclusionRef[];
  /** 已选历史结论 revision id 集合 */
  selectedRevisionIds: Set<string>;
  /** 切换历史结论勾选 */
  onToggleConclusion: (revisionId: string) => void;
  maxSelection?: number;
  /** 结论库变更回调（删除 / finalize 后刷新） */
  onConclusionsChanged?: () => void;
  /** 是否有数据快照（控制 SynthesisComposer 与提示文案） */
  hasSnapshot: boolean;
  /** 当前快照 ID（用于综合所选） */
  snapshotId: string;
  /** 综合创建成功回调 */
  onSynthesisCreated?: () => void;
}

export function ConclusionBarPanel({
  workspaceId,
  conclusions,
  selectedRevisionIds,
  onToggleConclusion,
  maxSelection = 20,
  onConclusionsChanged,
  hasSnapshot,
  snapshotId,
  onSynthesisCreated,
}: Props): JSX.Element {
  const [activeTab, setActiveTab] = useState<TabKey>('bar');

  const handleFinalized = (): void => {
    setActiveTab('library');
    onConclusionsChanged?.();
  };

  const items = [
    {
      key: 'bar' as const,
      label: '结论栏',
      children: (
        <ConclusionBar workspaceId={workspaceId} onFinalized={handleFinalized} />
      ),
    },
    {
      key: 'library' as const,
      label: `结论库 (${conclusions.length})`,
      children: (
        <>
          <ConclusionLibrary
            conclusions={conclusions}
            selectedRevisionIds={selectedRevisionIds}
            onToggle={onToggleConclusion}
            maxSelection={maxSelection}
            workspaceId={workspaceId}
            onDeleted={onConclusionsChanged}
          />
          {hasSnapshot && selectedRevisionIds.size >= 2 && (
            <div style={{ marginTop: 16 }}>
              <SynthesisComposer
                workspaceId={workspaceId}
                snapshotId={snapshotId}
                selectedRevisionIds={Array.from(selectedRevisionIds)}
                onCreated={onSynthesisCreated}
              />
            </div>
          )}
          {hasSnapshot && (
            <div
              style={{
                marginTop: 16,
                padding: 12,
                background: 'var(--ocean-surface-structural)',
                borderRadius: 6,
              }}
            >
              <Text type="secondary" style={{ fontSize: 13 }}>
                {'选择历史结论后可"用于下一轮"或"综合所选"。'}
              </Text>
            </div>
          )}
        </>
      ),
    },
  ];

  return (
    <Tabs
      activeKey={activeTab}
      onChange={(k) => setActiveTab(k as TabKey)}
      items={items}
      size="small"
    />
  );
}

export default ConclusionBarPanel;
