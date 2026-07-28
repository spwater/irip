import { Tabs, Typography } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { AssistantPage } from '@/assistant/AssistantPage';
import { AIToolsPage } from '@/ai_tools/AIToolsPage';
import { useAuthStore } from '@/auth/AuthProvider';

const { Title } = Typography;

const VALID_TABS = ['assistant', 'ai-tools'] as const;
type PlatformTab = (typeof VALID_TABS)[number];

/**
 * 平台应用页面
 *
 * 两个 Tab：AI 助手 / AI 工具管理。
 * "AI 工具管理" Tab 仅对 platform_administrator 角色可见（T-05），
 * 后端端点另由 system:manage 权限守卫。
 */
export function PlatformPage(): JSX.Element {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.roles?.includes('platform_administrator') ?? false;

  const tabRaw = (search as Record<string, unknown>).tab;
  const requestedTab = typeof tabRaw === 'string' ? tabRaw : '';
  const isValidTab = (VALID_TABS as readonly string[]).includes(requestedTab);
  // 非管理员不可激活 ai-tools Tab，回退到 assistant
  const activeTab: PlatformTab =
    isValidTab && !(requestedTab === 'ai-tools' && !isAdmin)
      ? (requestedTab as PlatformTab)
      : 'assistant';

  const handleTabChange = (key: string): void => {
    void navigate({ to: '/platform', search: { tab: key }, replace: true });
  };

  const items = [
    {
      key: 'assistant',
      label: 'AI助手',
      children: <AssistantPage />,
    },
    ...(isAdmin
      ? [
          {
            key: 'ai-tools',
            label: 'AI 工具管理',
            children: <AIToolsPage />,
          },
        ]
      : []),
  ];

  return (
    <div>
      <Title level={2}>平台应用</Title>
      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={items}
      />
    </div>
  );
}
