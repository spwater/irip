/**
 * WorkspaceDetail — 三栏布局
 *
 * 左栏: 数据快照
 * 中栏: 研究时间线（卡片按序号排列，展开显示问题+分析结果）
 * 右栏: 结论库
 */
import { useEffect, useState, useCallback } from 'react';
import { Button, Row, Col, Spin, message, Popconfirm, Tag, Typography, Modal } from 'antd';
import { ArrowLeftOutlined, DeleteOutlined, InboxOutlined } from '@ant-design/icons';
import { apiGetWorkspace, apiArchiveWorkspace, apiDeleteWorkspace, type WorkspaceDetail as WorkspaceDetailType } from '@/api/research';
import { http } from '@/api/client';
import { EvidencePanel } from './EvidencePanel';
import { WorkspaceTimeline } from './WorkspaceTimeline';
import { RecommendationPanel } from './RecommendationPanel';
import { ConclusionBarPanel } from './ConclusionBarPanel';
import { ResearchComposer } from './ResearchComposer';
import { TurnDetailPanel } from './TurnDetailPanel';

import type { ConclusionRef } from '@/api/researchTimeline';

const { Text } = Typography;

interface WorkspaceDetailProps {
  workspaceId: string;
  onBack: () => void;
}

export function WorkspaceDetail({ workspaceId, onBack }: WorkspaceDetailProps): JSX.Element {
  const [detail, setDetail] = useState<WorkspaceDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedRevisionIds, setSelectedRevisionIds] = useState<Set<string>>(new Set());
  const [composerQuestion, setComposerQuestion] = useState('');
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [timelineKey, setTimelineKey] = useState(0);
  const [recommendationRefreshKey, setRecommendationRefreshKey] = useState(0);
  const [conclusions, setConclusions] = useState<ConclusionRef[]>([]);

  const fetchConclusions = useCallback(async () => {
    try {
      const res = await http.get<{ items: ConclusionRef[] }>(
        `/research/workspaces/${workspaceId}/conclusions`,
      );
      setConclusions(res.data.items || []);
    } catch {
      // silent
    }
  }, [workspaceId]);

  useEffect(() => {
    fetchConclusions();
  }, [fetchConclusions, timelineKey]);

  const fetchDetail = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiGetWorkspace(workspaceId);
      setDetail(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败';
      message.error(msg);
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void fetchDetail();
    // Snapshot number changed → refresh recommendations
    if (detail?.latest_snapshot_number) {
      setRecommendationRefreshKey((k) => k + 1);
    }
  }, [fetchDetail]);

  const handleArchive = async () => {
    try {
      await apiArchiveWorkspace(workspaceId);
      message.success('已归档');
      onBack();
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { error?: { message?: string } } } };
      message.error(axiosErr?.response?.data?.error?.message || '归档失败');
    }
  };

  const handleDelete = async () => {
    try {
      await apiDeleteWorkspace(workspaceId);
      message.success('已删除');
      onBack();
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { error?: { message?: string } } } };
      message.error(axiosErr?.response?.data?.error?.message || '删除失败');
    }
  };

  const handleToggleConclusion = (revisionId: string) => {
    setSelectedRevisionIds((prev) => {
      const next = new Set(prev);
      if (next.has(revisionId)) next.delete(revisionId);
      else next.add(revisionId);
      return next;
    });
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!detail) {
    return <div style={{ padding: 24 }}>工作空间不存在</div>;
  }

  const hasSnapshot = detail.latest_snapshot_number != null && detail.latest_snapshot_number > 0;

  return (
    <div style={{ padding: 16 }}>
      {/* 顶部栏 */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center' }}>
        <Button icon={<ArrowLeftOutlined />} type="text" onClick={onBack}>
          返回列表
        </Button>
        <span style={{ fontSize: 18, fontWeight: 600, marginLeft: 8 }}>{detail.name}</span>
        <span style={{ marginLeft: 12 }}>
          <Tag color={detail.status === 'draft' ? 'blue' : 'default'}>
            {detail.status === 'draft' ? '活跃' : '已归档'}
          </Tag>
        </span>
        {hasSnapshot && (
          <Tag color="cyan" style={{ marginLeft: 4 }}>
            快照 v{detail.latest_snapshot_number}
          </Tag>
        )}
        {detail.turn_count > 0 && (
          <Tag style={{ marginLeft: 4 }}>{detail.turn_count} 轮研究</Tag>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {detail.status === 'draft' && (
            <Popconfirm
              title="归档后不可恢复为活跃"
              onConfirm={handleArchive}
              okText="确认归档"
              cancelText="取消"
            >
              <Button size="small" icon={<InboxOutlined />}>归档</Button>
            </Popconfirm>
          )}
          {detail.status === 'archived' && (
            <>
              <Button size="small" onClick={async () => {
                try {
                  await http.post(`/research/workspaces/${workspaceId}/restore`);
                  message.success('已恢复');
                  fetchDetail();
                } catch {
                  message.error('恢复失败');
                }
              }}>恢复</Button>
              <Popconfirm
                title="删除后不可恢复"
                onConfirm={handleDelete}
                okText="确认删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
              </Popconfirm>
            </>
          )}
        </div>
      </div>

      <Row gutter={16}>
        {/* 左栏：AI推荐 → 数据快照 */}
        <Col xs={24} lg={6}>
          {/* AI 推荐问题 */}
          {hasSnapshot && (
            <RecommendationPanel
              workspaceId={workspaceId}
              snapshotNumber={detail?.latest_snapshot_number ?? null}
              refreshKey={recommendationRefreshKey}
              onAdopt={(question) => {
                setComposerQuestion(question);
              }}
            />
          )}


          {/* 数据快照 */}
          <div style={{ marginTop: hasSnapshot ? 16 : 0 }}>
            <Text strong style={{ fontSize: 15, marginBottom: 8, display: 'block' }}>
              {'数据快照'}
            </Text>
            <EvidencePanel
              workspaceId={workspaceId}
              evidenceCount={detail.evidence_count}
              onEvidenceChanged={fetchDetail}
            />
          </div>
        </Col>

        {/* 中栏：研究时间线 */}
        <Col xs={24} lg={10}>
          {/* 提问区（位于时间线上方，新问题与已有问题按倒序排列） */}
          {hasSnapshot && (
            <div style={{ marginBottom: 16 }}>
              <ResearchComposer
                workspaceId={workspaceId}
                snapshotId={detail.snapshots?.[0]?.snapshot_id ?? ''}
                selectedRevisionIds={Array.from(selectedRevisionIds)}
                initialQuestion={composerQuestion}
                onTurnCreated={() => {
                  setSelectedRevisionIds(new Set());
                  setComposerQuestion('');
                  setTimelineKey((k) => k + 1);
                  setRecommendationRefreshKey((k) => k + 1);
                }}
              />
            </div>
          )}

          <Text strong style={{ fontSize: 15, marginBottom: 8, display: 'block' }}>
            {'研究时间线'}
          </Text>

          {/* 时间线卡片列表 */}
          <WorkspaceTimeline
            key={timelineKey}
            workspaceId={workspaceId}
            onTurnClick={(turnId) => {
              setSelectedTurnId(turnId === selectedTurnId ? null : turnId);
            }}
            onTurnCompleted={() => {
              setRecommendationRefreshKey((k) => k + 1);
            }}
          />

          {/* 选中卡片的展开详情（弹窗呈现，内容更宽） */}
          <Modal
            open={!!selectedTurnId}
            onCancel={() => setSelectedTurnId(null)}
            footer={null}
            width={1200}
            centered
            destroyOnHidden
            title={'研究分析报告'}
            styles={{ body: { maxHeight: '76vh', overflowY: 'auto', paddingTop: 8 } }}
          >
            {selectedTurnId && (
              <TurnDetailPanel
                workspaceId={workspaceId}
                turnId={selectedTurnId}
                onConclusionSaved={fetchConclusions}
              />
            )}
          </Modal>
        </Col>

        {/* 右栏：结论栏 + 结论库（Tab 切换） */}
        <Col xs={24} lg={8}>
          <ConclusionBarPanel
            workspaceId={workspaceId}
            conclusions={conclusions}
            selectedRevisionIds={selectedRevisionIds}
            onToggleConclusion={handleToggleConclusion}
            maxSelection={20}
            onConclusionsChanged={fetchConclusions}
            hasSnapshot={hasSnapshot}
            snapshotId={detail.snapshots?.[0]?.snapshot_id ?? ''}
            onSynthesisCreated={() => {
              setSelectedRevisionIds(new Set());
              setTimelineKey((k) => k + 1);
            }}
          />
        </Col>
      </Row>
    </div>
  );
}
