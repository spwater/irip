/**
 * ResultDetailView — 成果包详情页
 *
 * 左侧：衍生来源（Workspace/研究问题/源数据/Snapshot/Run/版本历史/权限变更记录）
 * 右侧：版本内容（metadata/points/series/Views/Insights Tab）
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Row,
  Col,
  Card,
  Tag,
  Space,
  Typography,
  Button,
  Spin,
  Tabs,
  Empty,
  message,
  Input,
  Drawer,
  Popconfirm,
} from 'antd';
import {
  ArrowLeftOutlined,
  EditOutlined,
  StarFilled,
  StarOutlined,
  HistoryOutlined,
  SafetyOutlined,
} from '@ant-design/icons';
import type { ResultDetail, ResultVersionDetail } from '@/api/researchPublish';
import {
  apiGetPublicationProvenance,
  apiUpdateResultMetadata,
  apiWithdrawResult,
  type ProvenanceInfo,
} from '@/api/researchPublish';
import { apiQueryResultProvenance } from '@/api/researchLineage';
import { ResultVersionHistory } from './ResultVersionHistory';
import { AclRevisionList } from './AclRevisionList';
import { PermissionEnvelopeView } from './PermissionEnvelopeView';
import { ProvenanceTab } from './ProvenanceTab';
import { tryParseStructured, StructuredConclusionDisplay } from './ConclusionLibrary';

const { Text, Paragraph } = Typography;

export type ResultDetailViewProps = {
  resultId: string;
  detail: ResultDetail;
  isFavorited: boolean;
  onBack: () => void;
  onFavoriteToggle: () => void;
  workspaceId?: string;
};

type VersionTab = 'metadata' | 'provenance';

export function ResultDetailView({
  resultId,
  detail,
  isFavorited,
  onBack,
  onFavoriteToggle,
  workspaceId,
}: ResultDetailViewProps): JSX.Element {
  const resultRef = detail.result;
  const currentVersion = detail.current_version;

  const [activeTab, setActiveTab] = useState<VersionTab>('metadata');
  const [versionDetail, setVersionDetail] = useState<ResultVersionDetail | null>(
    currentVersion ?? null,
  );
  const [provenance, setProvenance] = useState<ProvenanceInfo | null>(null);
  const [loadingProvenance, setLoadingProvenance] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(resultRef.name);
  const [savingName, setSavingName] = useState(false);
  const [aclDrawerOpen, setAclDrawerOpen] = useState(false);

  // 加载来源信息
  const fetchProvenance = useCallback(async () => {
    setLoadingProvenance(true);
    try {
      const info = await apiGetPublicationProvenance(resultId);
      setProvenance(info);
    } catch (err) {
      console.error('加载来源信息失败', err);
    } finally {
      setLoadingProvenance(false);
    }
  }, [resultId]);

  useEffect(() => {
    void fetchProvenance();
  }, [fetchProvenance]);

  // 版本选择回调
  const handleVersionSelect = useCallback((v: ResultVersionDetail) => {
    setVersionDetail(v);
    setActiveTab('metadata');
  }, []);

  // 加载内部对象（dataset/view/insight）
  // 保存名称编辑
  const handleSaveName = useCallback(async () => {
    if (!editName.trim()) {
      message.warning('名称不能为空');
      return;
    }
    if (!workspaceId) {
      message.info('仅在 Workspace 内可编辑');
      setEditing(false);
      return;
    }
    setSavingName(true);
    try {
      await apiUpdateResultMetadata(workspaceId, resultId, { name: editName.trim() });
      message.success('已保存');
      setEditing(false);
    } catch {
      message.error('保存失败');
    } finally {
      setSavingName(false);
    }
  }, [workspaceId, resultId, editName]);

  // 撤回成果
  const [withdrawing, setWithdrawing] = useState(false);
  const handleWithdraw = useCallback(async () => {
    setWithdrawing(true);
    try {
      await apiWithdrawResult(resultId);
      message.success('已撤回');
      window.location.reload();
    } catch {
      message.error('撤回失败');
    } finally {
      setWithdrawing(false);
    }
  }, [resultId]);

  const tabItems = [
    {
      key: 'metadata' as VersionTab,
      label: '数据预览',
      children: versionDetail ? (() => {
        const structured = versionDetail.summary
          ? tryParseStructured(versionDetail.summary)
          : null;
        if (structured) {
          return <StructuredConclusionDisplay data={structured} />;
        }
        return versionDetail.summary ? (
          <Paragraph style={{ fontSize: 13 }}>
            {versionDetail.summary}
          </Paragraph>
        ) : (
          <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        );
      })() : (
        <Empty description="暂无版本信息" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ),
    },
    {
      key: 'provenance' as VersionTab,
      label: '数据溯源',
      children: versionDetail ? (
        <ProvenanceTab
          fetchGraph={async (maxDepth: number) => {
            return apiQueryResultProvenance(
              resultId,
              versionDetail.version_number,
              maxDepth,
            );
          }}
          title="成果版本溯源"
          height={520}
        />
      ) : (
        <Empty description="无版本数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ),
    },
  ];

  return (
    <div>
      {/* 顶部导航栏 */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Button icon={<ArrowLeftOutlined />} type="text" onClick={onBack}>
          返回列表
        </Button>
        {editing ? (
          <Space>
            <Input
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              style={{ width: 240 }}
              placeholder="成果包名称"
            />
            <Button size="small" type="primary" loading={savingName} onClick={handleSaveName}>
              保存
            </Button>
            <Button size="small" onClick={() => { setEditing(false); setEditName(resultRef.name); }}>
              取消
            </Button>
          </Space>
        ) : (
          <>
            <Text strong style={{ fontSize: 16 }}>{resultRef.name}</Text>
            {workspaceId && (
              <Button
                size="small"
                type="text"
                icon={<EditOutlined />}
                onClick={() => setEditing(true)}
              />
            )}
          </>
        )}
        <Tag color={resultRef.status === 'published' ? 'green' : 'default'}>
          {resultRef.status === 'published' ? '已发布' : resultRef.status}
        </Tag>
        <Tag color="blue">v{resultRef.current_version}</Tag>
        <span
          role="button"
          tabIndex={0}
          onClick={onFavoriteToggle}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onFavoriteToggle();
            }
          }}
          style={{ cursor: 'pointer', fontSize: 16, color: isFavorited ? '#faad14' : '#bfbfbf' }}
        >
          {isFavorited ? <StarFilled /> : <StarOutlined />}
        </span>
        <div style={{ marginLeft: 'auto' }}>
          <Button
            size="small"
            icon={<SafetyOutlined />}
            onClick={() => setAclDrawerOpen(true)}
          >
            权限变更记录
          </Button>
          {resultRef.status === 'published' && (
            <Popconfirm
              title="确认撤回此成果包？"
              description="撤回后其他用户将无法看到此成果，数据保留可重新发布。"
              onConfirm={handleWithdraw}
              okText="撤回"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button
                size="small"
                danger
                loading={withdrawing}
                style={{ marginLeft: 8 }}
              >
                撤回
              </Button>
            </Popconfirm>
          )}
        </div>
      </div>

      {/* 主体：左右布局 */}
      <Row gutter={16}>
        {/* 左栏：衍生来源 */}
        <Col xs={24} lg={8}>
          <Space direction="vertical" size="small" style={{ width: '100%' }}>
            {/* 来源信息 */}
            <Card size="small" title="衍生来源" style={{ marginBottom: 12 }}>
              {loadingProvenance ? (
                <div style={{ textAlign: 'center', padding: 12 }}>
                  <Spin size="small" />
                </div>
              ) : provenance ? (
                <Space direction="vertical" size="small" style={{ width: '100%' }}>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>成果包 ID</Text>
                    <div>
                      <Text style={{ fontSize: 12 }}>
                        {provenance.result_id ?? resultId}
                      </Text>
                    </div>
                  </div>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>Evidence Snapshots</Text>
                    <div>
                      {provenance.evidence_snapshot_ids.length > 0 ? (
                        (provenance.evidence_snapshot_labels ?? provenance.evidence_snapshot_ids.map((sid) => ({ id: sid, label: sid.substring(0, 8) + '…' }))).map((s) => (
                          <Tag key={s.id} style={{ fontSize: 10, margin: '2px 4px 2px 0' }}>
                            {s.label}
                          </Tag>
                        ))
                      ) : (
                        <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
                      )}
                    </div>
                  </div>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>Analysis Runs</Text>
                    <div>
                      {provenance.analysis_run_ids.length > 0 ? (
                        (provenance.analysis_run_labels ?? provenance.analysis_run_ids.map((rid) => ({ id: rid, label: rid.substring(0, 8) + '…' }))).map((r) => (
                          <Tag key={r.id} style={{ fontSize: 10, margin: '2px 4px 2px 0' }}>
                            {r.label}
                          </Tag>
                        ))
                      ) : (
                        <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
                      )}
                    </div>
                  </div>
                  {Object.keys(provenance.source_run_statuses).length > 0 && (
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>Run 状态</Text>
                      <div>
                        {Object.entries(provenance.source_run_statuses).map(([rid, status]) => {
                          const runLabel = provenance.analysis_run_labels?.find((r) => r.id === rid)?.label ?? rid.substring(0, 8) + '…';
                          return (
                          <div key={rid} style={{ fontSize: 11 }}>
                            <Text code style={{ fontSize: 10 }}>{runLabel}</Text>
                            <Tag
                              color={status === 'succeeded' ? 'green' : status === 'failed' ? 'red' : 'default'}
                              style={{ fontSize: 10, margin: '0 4px' }}
                            >
                              {status}
                            </Tag>
                          </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </Space>
              ) : (
                <Empty description="无来源信息" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </Card>

            {/* 权限包络 */}
            {versionDetail && (
              <PermissionEnvelopeView
                envelope={versionDetail.published_permission_envelope}
                effectiveAcl={resultRef.current_acl_type}
              />
            )}

            {/* 版本历史 */}
            <ResultVersionHistory
              resultId={resultId}
              workspaceId={workspaceId}
              versionHistory={detail.version_history}
              onVersionSelect={handleVersionSelect}
            />
          </Space>
        </Col>

        {/* 右栏：版本内容 */}
        <Col xs={24} lg={16}>
          <Card
            size="small"
            title={
              <Space>
                <HistoryOutlined />
                <Text strong>
                  版本内容 v{versionDetail?.version_number ?? resultRef.current_version}
                </Text>
                {versionDetail && (
                  <Tag color={versionDetail.status === 'active' ? 'green' : versionDetail.status === 'withdrawn' ? 'red' : 'default'}>
                    {versionDetail.status}
                  </Tag>
                )}
              </Space>
            }
          >
            <Tabs
              activeKey={activeTab}
              onChange={(key) => setActiveTab(key as VersionTab)}
              items={tabItems}
              size="small"
            />
          </Card>
        </Col>
      </Row>

      {/* ACL 变更记录抽屉 */}
      <Drawer
        title="权限变更记录"
        open={aclDrawerOpen}
        onClose={() => setAclDrawerOpen(false)}
        width={500}
      >
        <AclRevisionList revisions={detail.acl_revisions} />
      </Drawer>
    </div>
  );
}
