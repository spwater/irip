import { Segmented, Tooltip } from 'antd';
import type { ConversationTab } from '@/api/collaboration';

/**
 * 对话列表三栏筛选标签组件（irip-ai-collab）。
 *
 * 三个选项：私有 / 同 org / 跨 org。
 * 跨 org 一期不可用（灰色 + Tooltip「二期上线」）。
 */
export function ConversationTabs({
  activeTab,
  onTabChange,
}: {
  activeTab: ConversationTab;
  onTabChange: (tab: ConversationTab) => void;
}): JSX.Element {
  return (
    <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--ocean-border-subtle, #f0f0f0)' }}>
      <Segmented
        size="small"
        value={activeTab}
        onChange={(val) => onTabChange(val as ConversationTab)}
        options={[
          { label: '私有', value: 'private' },
          { label: '同组织', value: 'same_org' },
          {
            label: (
              <Tooltip title="跨组织协作功能将在二期上线">
                <span style={{ color: 'var(--ocean-text-muted, #999)', cursor: 'not-allowed' }}>
                  跨组织
                </span>
              </Tooltip>
            ),
            value: 'cross_org',
            disabled: true,
          },
        ]}
      />
    </div>
  );
}

export default ConversationTabs;
