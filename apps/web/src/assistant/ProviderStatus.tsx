import { useState } from 'react';
import { Button, Descriptions, Modal, Tag, Typography } from 'antd';
import {
  useQuery,
} from '@tanstack/react-query';
import {
  apiGetProviderStatus,
  type ToolInfo,
} from '@/api/client';
import { StatusMark, DetailSection, FeedbackState } from '@/components/ui';
import type { StatusTone } from '@/theme/tokens';

const { Text } = Typography;

/**
 * Provider 状态组件
 *
 * 展示当前 AI Provider 模式（离线模拟/OpenAI 兼容），
 * 点击可展开查看可用工具列表（白名单 + 候选）。
 *
 * Data Ocean Phase 4：用 StatusMark + DetailSection + FeedbackState 替换 Badge/Card，
 * 保留 query / refresh / 文本不变。
 */
export function ProviderStatus(): JSX.Element {
  const [detailOpen, setDetailOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['assistant-provider-status'],
    queryFn: () => apiGetProviderStatus(),
    retry: false,
    refetchOnMount: true,
    staleTime: 0,
  });

  const providerMode = data?.provider_mode ?? 'offline';
  const isOffline = providerMode === 'offline';

  const providerTone: StatusTone = isOffline ? 'neutral' : 'success';
  const providerLabel = isOffline ? '离线模拟模式' : `OpenAI 兼容（${providerMode}）`;

  const whitelistCount = data?.whitelist_tools.length ?? 0;
  const candidateCount = data?.candidate_tools.length ?? 0;

  return (
    <>
      <div
        onClick={() => setDetailOpen(true)}
        style={{ cursor: 'pointer', marginBottom: 16 }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setDetailOpen(true);
          }
        }}
      >
        <StatusMark
          tone={providerTone}
          label={providerLabel}
          detail={`只读 ${whitelistCount} · 候选 ${candidateCount}`}
        />
      </div>

      <Modal
        title="中材小艾详情"
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={680}
      >
        {isLoading ? (
          <FeedbackState kind="loading" title="加载 Provider 状态中..." />
        ) : data ? (
          <div>
            <DetailSection title="运行模式">
              <Tag color={isOffline ? 'default' : 'blue'}>{providerMode}</Tag>
            </DetailSection>

            <DetailSection title="白名单工具（只读，可直接执行）">
              {data.whitelist_tools.map((tool: ToolInfo) => (
                <ToolRow key={tool.name} tool={tool} />
              ))}
            </DetailSection>

            <DetailSection title="候选工具（需人工审批）">
              {data.candidate_tools.map((tool: ToolInfo) => (
                <ToolRow key={tool.name} tool={tool} />
              ))}
            </DetailSection>
          </div>
        ) : (
          <FeedbackState
            kind="error"
            title="无法获取 Provider 状态"
            description="请检查后端服务是否正常运行"
            onRetry={() => setDetailOpen(false)}
          />
        )}
      </Modal>
    </>
  );
}

/** 工具信息行（替代原 ToolCard，用语义化结构展示） */
function ToolRow({ tool }: { tool: ToolInfo }): JSX.Element {
  const tone: StatusTone = tool.candidate ? 'warning' : 'success';
  const label = tool.candidate ? '候选' : '只读';

  return (
    <div
      style={{
        marginBottom: 8,
        padding: '8px 12px',
        borderRadius: 4,
        background: 'rgba(240, 250, 251, 0.72)',
        border: '1px solid rgba(24, 102, 133, 0.16)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Text strong>{tool.display_name}</Text>
        <StatusMark tone={tone} label={label} />
      </div>
      <div>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {tool.name}
        </Text>
      </div>
      <div>
        <Text style={{ fontSize: 13 }}>{tool.description}</Text>
      </div>
      <div>
        <Tag style={{ fontSize: 11 }}>{tool.required_permission}</Tag>
      </div>
    </div>
  );
}

export default ProviderStatus;
