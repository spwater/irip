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
  Tooltip,
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
  CopyOutlined,
  DatabaseOutlined,
  BarChartOutlined,
  BulbOutlined,
  NodeIndexOutlined,
} from '@ant-design/icons';
import type { ResultDetail, ResultVersionDetail } from '@/api/researchPublish';
import {
  apiGetPublicationProvenance,
  apiGetPublicationItem,
  apiUpdateResultMetadata,
  apiWithdrawResult,
  type ProvenanceInfo,
} from '@/api/researchPublish';
import { apiQueryResultProvenance } from '@/api/researchLineage';
import { ResultVersionHistory } from './ResultVersionHistory';
import { AclRevisionList } from './AclRevisionList';
import { PermissionEnvelopeView } from './PermissionEnvelopeView';
import { ProvenanceTab } from './ProvenanceTab';

const { Text, Paragraph } = Typography;

export type ResultDetailViewProps = {
  resultId: string;
  detail: ResultDetail;
  isFavorited: boolean;
  onBack: () => void;
  onFavoriteToggle: () => void;
  workspaceId?: string;
};

type VersionTab = 'metadata' | 'datasets' | 'views' | 'insights' | 'provenance';

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
  const [itemDetails, setItemDetails] = useState<Record<string, Record<string, unknown>>>({});
  const [loadingItem, setLoadingItem] = useState<string | null>(null);
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
  const handleLoadItem = useCallback(
    async (itemType: string, itemId: string) => {
      const cacheKey = `${itemType}:${itemId}`;
      if (itemDetails[cacheKey]) return;
      setLoadingItem(cacheKey);
      try {
        const data = await apiGetPublicationItem(resultId, itemType, itemId);
        setItemDetails((prev) => ({ ...prev, [cacheKey]: data }));
      } catch {
        message.error('加载对象详情失败');
      } finally {
        setLoadingItem(null);
      }
    },
    [resultId, itemDetails],
  );

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

  const datasetRefs = versionDetail?.dataset_version_refs ?? [];
  const viewRefs = versionDetail?.view_version_refs ?? [];
  const insightRefs = versionDetail?.insight_version_refs ?? [];

  const tabItems = [
    {
      key: 'metadata' as VersionTab,
      label: (
        <Space size={4}>
          <span>版本信息</span>
        </Space>
      ),
      children: versionDetail ? (
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {versionDetail.summary && (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>摘要</Text>
              <Paragraph style={{ margin: '4px 0', fontSize: 13 }}>
                {versionDetail.summary}
              </Paragraph>
            </div>
          )}
          {versionDetail.tags.length > 0 && (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>标签</Text>
              <div style={{ marginTop: 4 }}>
                <Space size={4} wrap>
                  {versionDetail.tags.map((tag) => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                </Space>
              </div>
            </div>
          )}
          {versionDetail.release_notes && (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>发布说明</Text>
              <Paragraph style={{ margin: '4px 0', fontSize: 13, whiteSpace: 'pre-wrap' }}>
                {versionDetail.release_notes}
              </Paragraph>
            </div>
          )}
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>内容哈希</Text>
            <div style={{ marginTop: 2 }}>
              <Space>
                <Text code style={{ fontSize: 11 }}>
                  {versionDetail.content_hash.substring(0, 32)}…
                </Text>
                <Tooltip title="复制哈希">
                  <Button
                    size="small"
                    type="text"
                    icon={<CopyOutlined />}
                    onClick={() => {
                      void navigator.clipboard.writeText(versionDetail.content_hash);
                      message.success('已复制');
                    }}
                  />
                </Tooltip>
              </Space>
            </div>
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>发布者</Text>
            <div style={{ marginTop: 2 }}>
              <Text style={{ fontSize: 12 }}>{versionDetail.publisher}</Text>
            </div>
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>发布时间</Text>
            <div style={{ marginTop: 2 }}>
              <Text style={{ fontSize: 12 }}>
                {versionDetail.published_at
                  ? new Date(versionDetail.published_at).toLocaleString()
                  : '—'}
              </Text>
            </div>
          </div>
        </Space>
      ) : (
        <Empty description="暂无版本信息" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ),
    },
    {
      key: 'datasets' as VersionTab,
      label: (
        <Space size={4}>
          <DatabaseOutlined />
          <span>数据集 ({datasetRefs.length})</span>
        </Space>
      ),
      children: datasetRefs.length === 0 ? (
        <Empty description="无数据集" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {datasetRefs.map((ref, idx) => {
            const dsId = String(ref.dataset_id ?? '');
            const vn = Number(ref.version_number ?? 0);
            const cacheKey = `dataset:${dsId}`;
            const itemData = itemDetails[cacheKey];
            return (
              <Card
                key={`ds-${idx}`}
                size="small"
                style={{ marginBottom: 8 }}
                title={
                  <Space>
                    <Tag color="blue">v{vn}</Tag>
                    <Text style={{ fontSize: 13 }}>{dsId.substring(0, 8)}…</Text>
                  </Space>
                }
              >
                {loadingItem === cacheKey ? (
                  <Spin size="small" />
                ) : itemData ? (
                  <pre style={{ fontSize: 11, maxHeight: 200, overflow: 'auto', margin: 0 }}>
                    {JSON.stringify(itemData, null, 2)}
                  </pre>
                ) : (
                  <Button
                    size="small"
                    type="link"
                    onClick={() => handleLoadItem('dataset', dsId)}
                  >
                    加载详情
                  </Button>
                )}
              </Card>
            );
          })}
        </Space>
      ),
    },
    {
      key: 'views' as VersionTab,
      label: (
        <Space size={4}>
          <BarChartOutlined />
          <span>视图 ({viewRefs.length})</span>
        </Space>
      ),
      children: viewRefs.length === 0 ? (
        <Empty description="无视图" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {viewRefs.map((ref, idx) => {
            const vId = String(ref.view_id ?? '');
            const vn = Number(ref.version_number ?? 0);
            const cacheKey = `view:${vId}`;
            const itemData = itemDetails[cacheKey];
            return (
              <Card
                key={`vw-${idx}`}
                size="small"
                style={{ marginBottom: 8 }}
                title={
                  <Space>
                    <Tag color="blue">v{vn}</Tag>
                    <Text style={{ fontSize: 13 }}>{vId.substring(0, 8)}…</Text>
                  </Space>
                }
              >
                {loadingItem === cacheKey ? (
                  <Spin size="small" />
                ) : itemData ? (
                  <pre style={{ fontSize: 11, maxHeight: 200, overflow: 'auto', margin: 0 }}>
                    {JSON.stringify(itemData, null, 2)}
                  </pre>
                ) : (
                  <Button
                    size="small"
                    type="link"
                    onClick={() => handleLoadItem('view', vId)}
                  >
                    加载详情
                  </Button>
                )}
              </Card>
            );
          })}
        </Space>
      ),
    },
    {
      key: 'insights' as VersionTab,
      label: (
        <Space size={4}>
          <BulbOutlined />
          <span>Insights ({insightRefs.length})</span>
        </Space>
      ),
      children: insightRefs.length === 0 ? (
        <Empty description="无 Insight" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {insightRefs.map((ref, idx) => {
            const iId = String(ref.insight_id ?? '');
            const vn = Number(ref.version_number ?? 0);
            const cacheKey = `insight:${iId}`;
            const itemData = itemDetails[cacheKey];
            return (
              <Card
                key={`ins-${idx}`}
                size="small"
                style={{ marginBottom: 8 }}
                title={
                  <Space>
                    <Tag color="blue">v{vn}</Tag>
                    <Text style={{ fontSize: 13 }}>{iId.substring(0, 8)}…</Text>
                  </Space>
                }
              >
                {loadingItem === cacheKey ? (
                  <Spin size="small" />
                ) : itemData ? (
                  <pre style={{ fontSize: 11, maxHeight: 200, overflow: 'auto', margin: 0 }}>
                    {JSON.stringify(itemData, null, 2)}
                  </pre>
                ) : (
                  <Button
                    size="small"
                    type="link"
                    onClick={() => handleLoadItem('insight', iId)}
                  >
                    加载详情
                  </Button>
                )}
              </Card>
            );
          })}
        </Space>
      ),
    },
    {
      key: 'provenance' as VersionTab,
      label: (
        <Space size={4}>
          <NodeIndexOutlined />
          <span>数据溯源</span>
        </Space>
      ),
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
          onClick={onFavoriteToggle}
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
