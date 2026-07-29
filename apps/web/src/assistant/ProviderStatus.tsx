import { useState } from 'react';
import { Badge, Card, Descriptions, Modal, Tag, Typography } from 'antd';
import {
  useQuery,
} from '@tanstack/react-query';
import { apiGetProviderStatus, type ToolInfo } from '@/api/models-ai';

const { Text, Title } = Typography;

/**
 * Provider 状态组件
 *
 * 展示当前 AI Provider 模式（离线模拟/OpenAI 兼容），
 * 点击可展开查看可用工具列表（白名单 + 候选）。
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

  return (
    <>
      <Card
        size="small"
        style={{ marginBottom: 16 }}
        onClick={() => setDetailOpen(true)}
        hoverable
      >
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="中材小艾">
            <Badge
              status={isOffline ? 'default' : 'processing'}
              text={
                isOffline ? '离线模拟模式' : `OpenAI 兼容（${providerMode}）`
              }
            />
          </Descriptions.Item>
          <Descriptions.Item label="可用工具">
            <Tag color="green">只读 {data?.whitelist_tools.length ?? 0}</Tag>
            <Tag color="orange">候选 {data?.candidate_tools.length ?? 0}</Tag>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Modal
        title="中材小艾详情"
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={680}
      >
        {isLoading ? (
          <Text type="secondary">加载中...</Text>
        ) : data ? (
          <div>
            <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="运行模式">
                <Tag color={isOffline ? 'default' : 'blue'}>{providerMode}</Tag>
              </Descriptions.Item>
            </Descriptions>

            <Title level={5}>白名单工具（只读，可直接执行）</Title>
            {data.whitelist_tools.map((tool: ToolInfo) => (
              <ToolCard key={tool.name} tool={tool} />
            ))}

            <Title level={5} style={{ marginTop: 16 }}>
              候选工具（需人工审批）
            </Title>
            {data.candidate_tools.map((tool: ToolInfo) => (
              <ToolCard key={tool.name} tool={tool} />
            ))}
          </div>
        ) : (
          <Text type="secondary">无法获取 Provider 状态</Text>
        )}
      </Modal>
    </>
  );
}

/** 工具信息卡片 */
function ToolCard({ tool }: { tool: ToolInfo }): JSX.Element {
  return (
    <Card
      size="small"
      style={{ marginBottom: 8 }}
      bodyStyle={{ padding: '8px 12px' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <Text strong>{tool.display_name}</Text>
        <Tag color={tool.candidate ? 'orange' : 'green'}>
          {tool.candidate ? '候选' : '只读'}
        </Tag>
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
    </Card>
  );
}

export default ProviderStatus;
