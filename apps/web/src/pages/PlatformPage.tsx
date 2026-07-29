import { Tabs } from 'antd';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { AssistantPage } from '@/assistant/AssistantPage';
import { AIToolsPage } from '@/ai_tools/AIToolsPage';
import { ComponentsPage } from '@/components/ComponentsPage';
import { useAuthStore } from '@/auth/AuthProvider';
import { PageIntro } from '@/components/ui';

const VALID_TABS = ['assistant', 'ai-tools', 'components'] as const;
type PlatformTab = (typeof VALID_TABS)[number];

/**
 * 平台应用页面（设计文档第 10.6 节）
 *
 * 三个 Tab：AI 助手 / AI 工具管理 / 数据接口。
 * "AI 工具管理" Tab 仅对 platform_administrator 角色可见（T-05），
 * 后端端点另由 system:manage 权限守卫。
 * "数据接口" Tab（ComponentsPage）从实验室建设移入，支持 prefill_object 与 edit_id 深链。
 */
export function PlatformPage(): JSX.Element {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.roles?.includes('platform_administrator') ?? false;

  const tabRaw = (search as Record<string, unknown>).tab;
  const prefillObjectRaw = (search as Record<string, unknown>).prefill_object;
  const prefillObject: string | undefined =
    typeof prefillObjectRaw === 'string' ? prefillObjectRaw : undefined;
  const editIdRaw = (search as Record<string, unknown>).edit_id;
  const editId: string | undefined =
    typeof editIdRaw === 'string' ? editIdRaw : undefined;

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
            label: '工具插件',
            children: <AIToolsPage />,
          },
        ]
      : []),
    {
      key: 'components',
      label: '数据接口',
      children: <ComponentsPage prefillObject={prefillObject} editId={editId} />,
    },
  ];

  return (
    <div className="ocean-page-enter">
      <PageIntro
        index="MODULE 04 / PLATFORM APPLICATIONS"
        title="平台应用"
      />
      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={items}
      />
    </div>
  );
}
