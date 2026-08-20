import { useMemo } from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { AssistantPage } from '@/features/assistant/AssistantPage';
import { AIToolsPage } from '@/features/ai-tools/AIToolsPage';
import { PersonalSettings } from '@/features/platform/PersonalSettings';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { usePageHeaderRegistration } from '@/app/PageHeaderContext';

const VALID_TABS = ['assistant', 'ai-tools', 'personal-settings'] as const;
type PlatformTab = (typeof VALID_TABS)[number];

/**
 * 平台应用页面
 *
 * 三个 Tab：AI助手 / 工具插件 / 个人设置
 * Tab 切换和页面标题注册到 AppShell Header。
 *
 * M-09 整改：使用 usePageHeaderRegistration，unmount 时清空 header。
 */
export function PlatformPage(): JSX.Element {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.roles?.includes('platform_administrator') ?? false;

  const tabRaw = (search as Record<string, unknown>).tab;

  const requestedTab = typeof tabRaw === 'string' ? tabRaw : '';
  const isValidTab = (VALID_TABS as readonly string[]).includes(requestedTab);
  const activeTab: PlatformTab =
    isValidTab && !(requestedTab === 'ai-tools' && !isAdmin)
      ? (requestedTab as PlatformTab)
      : 'assistant';

  const handleTabChange = (key: string): void => {
    void navigate({ to: '/platform', search: { tab: key }, replace: true });
  };

  const tabs = useMemo(() => {
    const items = [
      { key: 'assistant', label: 'AI助手' },
      ...(isAdmin ? [{ key: 'ai-tools', label: '工具插件' }] : []),
      // irip-ai-collab: 个人设置（所有用户可见）
      { key: 'personal-settings', label: '个人设置' },
    ];
    return items;
  }, [isAdmin]);

  usePageHeaderRegistration(
    {
      index: 'MODULE 04 / PLATFORM APPLICATIONS',
      title: '平台应用',
      tabs,
      activeTab,
      onTabChange: handleTabChange,
    },
    [activeTab, tabs],
  );

  return (
    <div
      className="ocean-page-enter"
      key={activeTab}
      style={activeTab === 'assistant' ? { overflow: 'hidden', height: '100%' } : { minHeight: 'calc(100vh - 200px)' }}
    >
      {activeTab === 'assistant' && <AssistantPage />}
      {activeTab === 'ai-tools' && isAdmin && <AIToolsPage />}
      {activeTab === 'personal-settings' && <PersonalSettings />}
    </div>
  );
}
