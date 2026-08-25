/**
 * ConclusionBarPanel — 右栏 Tab 容器（结论栏 / 结论库 / 发布成果）。
 *
 * Ant Design Tabs：
 * - Tab1 "结论栏"：ConclusionBar（新）
 * - Tab2 "结论库"：ConclusionLibrary + 发布结果按钮（替代原 SynthesisComposer）
 * - Tab3 "发布成果"：已发布成果列表 + 详情 Modal
 *
 * finalize 成功后自动切换到结论库 Tab，并刷新 conclusions 列表。
 * 发布成功后自动切换到发布成果 Tab，并刷新 results 列表。
 */
import { useState } from 'react';
import { Button, Card, Empty, Popconfirm, Spin, Tabs, Tag, Typography, message } from 'antd';
import { DeleteOutlined, SendOutlined, UndoOutlined, GlobalOutlined, LockOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ConclusionBar } from './ConclusionBar';
import { ResultDetailModal } from './ResultDetailModal';
import { apiDeleteResult, apiListResults, apiRepublishResult, apiWithdrawResult } from '@/api/researchResults';
import { apiUpdateAcl } from '@/api/researchPublish';

const { Text } = Typography;

type TabKey = 'bar' | 'library';

interface Props {
  workspaceId: string;
}
/** 把 UTC 时间字符串转成本地时间显示 */
function fmtTime(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString('zh-CN', { hour12: false });
}

export function ConclusionBarPanel({
  workspaceId,
}: Props): JSX.Element {
  const [activeTab, setActiveTab] = useState<TabKey>('bar');
  const [detailResultId, setDetailResultId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const queryClient = useQueryClient();

  // 发布成果列表
  const { data: results, isLoading: resultsLoading } = useQuery({
    queryKey: ['research-results', workspaceId],
    queryFn: () => apiListResults(workspaceId),
  });

  const handleFinalized = (): void => {
    setActiveTab('library');
    void queryClient.invalidateQueries({ queryKey: ['research-results', workspaceId] });
  };

  const handleOpenDetail = (resultId: string): void => {
    setDetailResultId(resultId);
    setDetailOpen(true);
  };

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['research-results', workspaceId] });
  };

  const withdrawMutation = useMutation({
    mutationFn: (resultId: string) => apiWithdrawResult(workspaceId, resultId),
    onSuccess: () => { message.success('已撤回'); refresh(); },
    onError: () => message.error('撤回失败'),
  });

  const republishMutation = useMutation({
    mutationFn: (resultId: string) => apiRepublishResult(workspaceId, resultId),
    onSuccess: () => { message.success('已发布'); refresh(); },
    onError: () => message.error('发布失败'),
  });

  const deleteMutation = useMutation({
    mutationFn: (resultId: string) => apiDeleteResult(workspaceId, resultId),
    onSuccess: () => { message.success('已删除'); setDetailOpen(false); refresh(); },
    onError: () => message.error('删除失败'),
  });

  const aclMutation = useMutation({
    mutationFn: (params: { resultId: string; acl: string }) =>
      apiUpdateAcl(workspaceId, params.resultId, { acl_type: params.acl }),
    onSuccess: () => { message.success('权限已更新'); refresh(); },
    onError: () => message.error('操作失败'),
  });


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
      label: `结论库 (${results?.length ?? 0})`,
      children: resultsLoading ? (
        <div style={{ textAlign: 'center', padding: 32 }}>
          <Spin tip="加载中…" />
        </div>
      ) : !results || results.length === 0 ? (
        <Empty
          description="暂无结论，请在结论栏中推送并生成最终结论"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <div data-testid="result-list">
          {results.map((r) => (
            <Card
              key={r.id}
              size="small"
              hoverable
              style={{ marginBottom: 8 }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div
                  style={{ flex: 1, minWidth: 0, cursor: 'pointer' }}
                  onClick={() => handleOpenDetail(r.id)}
                >
                  <Text strong>{r.name}</Text>
                  <div style={{ marginTop: 4, fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Tag color={r.status === 'published' ? 'green' : 'default'}>
                      {r.status === 'published' ? '已发布' : '已撤回'}
                    </Tag>
                    {r.current_acl_type === 'all' ? (
                      <Tag color="green" style={{ margin: 0, fontSize: 11 }} icon={<GlobalOutlined />}>公开</Tag>
                    ) : (
                      <Tag color="default" style={{ margin: 0, fontSize: 11 }} icon={<LockOutlined />}>私有</Tag>
                    )}
                    <Text type="secondary" style={{ marginLeft: 4 }}>
                      {fmtTime(r.created_at)}
                    </Text>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                  {r.current_acl_type !== 'all' ? (
                    <Popconfirm
                      title="确认设为公开？"
                      description="公开后所有用户可在载入数据时查看此成果。"
                      onConfirm={() => aclMutation.mutate({ resultId: r.id, acl: 'all' })}
                      okText="确认"
                      cancelText="取消"
                    >
                      <Button
                        size="small"
                        type="text"
                        icon={<GlobalOutlined />}
                        loading={aclMutation.isPending && aclMutation.variables?.resultId === r.id}
                        onClick={(e) => e.stopPropagation()}
                        style={{ color: '#1890ff' }}
                      />
                    </Popconfirm>
                  ) : (
                    <Popconfirm
                      title="确认设为私有？"
                      description="设为私有后仅自己可见。"
                      onConfirm={() => aclMutation.mutate({ resultId: r.id, acl: 'private' })}
                      okText="确认"
                      cancelText="取消"
                    >
                      <Button
                        size="small"
                        type="text"
                        icon={<LockOutlined />}
                        loading={aclMutation.isPending && aclMutation.variables?.resultId === r.id}
                        onClick={(e) => e.stopPropagation()}
                        style={{ color: '#8c8c8c' }}
                      />
                    </Popconfirm>
                  )}
                  {r.status === 'withdrawn' && (
                    <Button
                      size="small"
                      type="text"
                      icon={<SendOutlined />}
                      loading={republishMutation.isPending && republishMutation.variables === r.id}
                      onClick={(e) => { e.stopPropagation(); republishMutation.mutate(r.id); }}
                      style={{ color: '#52c41a' }}
                    />
                  )}
                  {r.status === 'published' && (
                    <Popconfirm
                      title="确认撤回此成果？"
                      onConfirm={() => withdrawMutation.mutate(r.id)}
                      okText="撤回"
                      cancelText="取消"
                    >
                      <Button
                        size="small"
                        type="text"
                        icon={<UndoOutlined />}
                        loading={withdrawMutation.isPending && withdrawMutation.variables === r.id}
                        onClick={(e) => e.stopPropagation()}
                        style={{ color: '#faad14' }}
                      />
                    </Popconfirm>
                  )}
                  <Popconfirm
                    title="确认永久删除此成果？"
                    description="删除后不可恢复"
                    onConfirm={() => deleteMutation.mutate(r.id)}
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                  >
                    <Button
                      size="small"
                      type="text"
                      icon={<DeleteOutlined />}
                      loading={deleteMutation.isPending && deleteMutation.variables === r.id}
                      onClick={(e) => e.stopPropagation()}
                      danger
                    />
                  </Popconfirm>
                </div>
              </div>
            </Card>
          ))}
        </div>
      ),
    },
  ];

  return (
    <>
      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as TabKey)}
        items={items}
        size="small"
      />
      <ResultDetailModal
        workspaceId={workspaceId}
        resultId={detailResultId}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
      />
    </>
  );
}

export default ConclusionBarPanel;
