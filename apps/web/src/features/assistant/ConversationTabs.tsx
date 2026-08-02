import { Segmented } from 'antd';

/**
 * 对话列表两栏筛选标签组件。
 *
 * 两个选项：私有 / 协同。
 * - 私有：仅自己参与的对话（参与者 <= 1）
 * - 协同：有多人参与的对话（参与者 > 1）
 */
export function ConversationTabs({
  activeTab,
  onTabChange,
}: {
  activeTab: 'private' | 'collaborative';
  onTabChange: (tab: 'private' | 'collaborative') => void;
}): JSX.Element {
  return (
    <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--ocean-border-subtle, #f0f0f0)' }}>
      <Segmented
        size="small"
        value={activeTab}
        onChange={(val) => onTabChange(val as 'private' | 'collaborative')}
        options={[
          { label: '私有', value: 'private' },
          { label: '协同', value: 'collaborative' },
        ]}
      />
    </div>
  );
}

export default ConversationTabs;
