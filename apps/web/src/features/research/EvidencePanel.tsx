/**
 * 左栏：数据面板
 *
 * 点击「添加数据」→ 弹窗树形多选（项目→任务→数据，复用 AI 助手 FactDataModal 模式）
 * 已选数据列表 → 每项显示源名称/版本/权限状态 + 删除按钮
 * 底部「冻结快照」按钮
 * 冻结后切换为只读快照视图
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, List, Spin, Tag, Modal, Checkbox, Input, Typography, Radio, Space, message } from 'antd';
import { PlusOutlined, DeleteOutlined, CameraOutlined, SearchOutlined } from '@ant-design/icons';
import {
  apiListEvidence,
  apiAddEvidence,
  apiRemoveEvidence,
  apiFreezeSnapshot,
  apiListSnapshots,
  type EvidenceRef,
  type Snapshot,
} from '@/api/research';
import { apiListFacts } from '@/api/facts-provenance';
import { apiSearchCatalog, type CatalogSearchResult } from '@/api/researchProducts';
import type { FactSummary } from '@/api/types';

const { Text } = Typography;

interface EvidencePanelProps {
  workspaceId: string;
  evidenceCount: number;
  onEvidenceChanged: () => void;
}

// ---- 树形分组类型（复用 AI 助手模式） ----
type FactItem = { fact_id: string; subject_id: string };
type TaskGroup = { taskName: string; facts: FactItem[] };
type ProjectGroup = { projectName: string; tasks: Record<string, TaskGroup> };
type FactGroups = Record<string, ProjectGroup>;

function buildFactGroups(allFacts: FactSummary[], searchText: string): FactGroups {
  const filtered = searchText.trim()
    ? allFacts.filter(
        (f) =>
          f.subject_id.toLowerCase().includes(searchText.toLowerCase()) ||
          (f.task_name ?? '').toLowerCase().includes(searchText.toLowerCase()) ||
          (f.project_name ?? '').toLowerCase().includes(searchText.toLowerCase()),
      )
    : allFacts;

  const groups: FactGroups = {};
  for (const f of filtered) {
    const projKey = f.project_name ?? '未分类项目';
    const taskKey = f.task_code ?? '未分组';
    if (!groups[projKey]) groups[projKey] = { projectName: projKey, tasks: {} };
    if (!groups[projKey].tasks[taskKey]) {
      groups[projKey].tasks[taskKey] = { taskName: f.task_name ?? taskKey, facts: [] };
    }
    groups[projKey].tasks[taskKey].facts.push({ fact_id: f.fact_id, subject_id: f.subject_id });
  }
  return groups;
}

function flattenFactIds(groups: FactGroups): string[] {
  return Object.values(groups).flatMap((p) =>
    Object.values(p.tasks).flatMap((t) => t.facts.map((f) => f.fact_id)),
  );
}

export function EvidencePanel({ workspaceId, evidenceCount, onEvidenceChanged }: EvidencePanelProps): JSX.Element {
  const [evidenceRefs, setEvidenceRefs] = useState<EvidenceRef[]>([]);
  const [loadingEvidence, setLoadingEvidence] = useState(false);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [freezing, setFreezing] = useState(false);

  // 弹窗状态
  const [modalOpen, setModalOpen] = useState(false);
  const [allFacts, setAllFacts] = useState<FactSummary[]>([]);
  const [loadingFacts, setLoadingFacts] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [selectedFactIds, setSelectedFactIds] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const [evidenceType, setEvidenceType] = useState<'fact' | 'derived'>('fact');
  const [derivedResults, setDerivedResults] = useState<CatalogSearchResult[]>([]);
  const [selectedDerivedIds, setSelectedDerivedIds] = useState<string[]>([]);
  const [loadingDerived, setLoadingDerived] = useState(false);

  const fetchEvidence = useCallback(async () => {
    setLoadingEvidence(true);
    try {
      const res = await apiListEvidence(workspaceId);
      setEvidenceRefs(res?.items ?? []);
    } catch {
      message.error('加载数据列表失败');
    } finally {
      setLoadingEvidence(false);
    }
  }, [workspaceId]);

  const fetchSnapshots = useCallback(async () => {
    try {
      const res = await apiListSnapshots(workspaceId);
      setSnapshots(res?.items ?? []);
    } catch {
      // 静默
    }
  }, [workspaceId]);

  useEffect(() => {
    void fetchEvidence();
    void fetchSnapshots();
  }, [fetchEvidence, fetchSnapshots]);

  // 打开弹窗时加载全部 Fact
  const handleOpenModal = async () => {
    setModalOpen(true);
    setEvidenceType('fact');
    setSelectedFactIds([]);
    setSelectedDerivedIds([]);
    setLoadingFacts(true);
    try {
      const res = await apiListFacts({ page_size: 100, status: 'active' });
      setAllFacts(res?.items ?? []);
    } catch {
      message.error('加载数据列表失败');
    } finally {
      setLoadingFacts(false);
    }
  };

  // 搜索衍生数据
  const handleSearchDerived = useCallback(async (query: string) => {
    setLoadingDerived(true);
    try {
      const res = await apiSearchCatalog(query);
      setDerivedResults(res?.items ?? []);
    } catch {
      message.error('搜索衍生数据失败');
    } finally {
      setLoadingDerived(false);
    }
  }, []);

  // 切换证据类型时加载衍生数据
  useEffect(() => {
    if (evidenceType === 'derived' && derivedResults.length === 0) {
      void handleSearchDerived('');
    }
  }, [evidenceType, derivedResults.length, handleSearchDerived]);

  const factGroups = useMemo(() => buildFactGroups(allFacts, searchText), [allFacts, searchText]);
  const allFilteredFactIds = useMemo(() => flattenFactIds(factGroups), [factGroups]);
  const allSelected = allFilteredFactIds.length > 0 && allFilteredFactIds.every((id) => selectedFactIds.includes(id));
  const someSelected = allFilteredFactIds.some((id) => selectedFactIds.includes(id));

  const toggleFact = (factId: string) => {
    setSelectedFactIds((prev) =>
      prev.includes(factId) ? prev.filter((id) => id !== factId) : [...prev, factId],
    );
  };

  const toggleGroup = (groupIds: string[]) => {
    const allIn = groupIds.every((id) => selectedFactIds.includes(id));
    if (allIn) {
      setSelectedFactIds((prev) => prev.filter((id) => !groupIds.includes(id)));
    } else {
      setSelectedFactIds((prev) => [...new Set([...prev, ...groupIds])]);
    }
  };

  const selectAll = () => {
    if (allSelected) {
      setSelectedFactIds([]);
    } else {
      setSelectedFactIds(allFilteredFactIds);
    }
  };

  // 确认添加选中的数据
  const handleConfirmAdd = async () => {
    if (evidenceType === 'fact') {
      if (selectedFactIds.length === 0) {
        message.warning('请至少选择一条数据');
        return;
      }
      setAdding(true);
      let successCount = 0;
      let failCount = 0;
      for (const factId of selectedFactIds) {
        try {
          await apiAddEvidence(workspaceId, { source_namespace: 'core:fact', source_id: factId });
          successCount++;
        } catch {
          failCount++;
        }
      }
      setAdding(false);
      if (successCount > 0) message.success(`已加入 ${successCount} 条数据`);
      if (failCount > 0) message.warning(`${failCount} 条加入失败（可能已存在）`);
      setSelectedFactIds([]);
    } else {
      // 衍生数据
      if (selectedDerivedIds.length === 0) {
        message.warning('请至少选择一个衍生数据集');
        return;
      }
      setAdding(true);
      let successCount = 0;
      let failCount = 0;
      for (const dsId of selectedDerivedIds) {
        const dsInfo = derivedResults.find((d) => d.id === dsId);
        try {
          await apiAddEvidence(workspaceId, {
            source_namespace: 'research:derived',
            source_id: dsId,
          });
          successCount++;
        } catch {
          failCount++;
        }
      }
      setAdding(false);
      if (successCount > 0) message.success(`已加入 ${successCount} 个衍生数据集`);
      if (failCount > 0) message.warning(`${failCount} 个加入失败`);
      setSelectedDerivedIds([]);
    }
    setModalOpen(false);
    void fetchEvidence();
    onEvidenceChanged();
  };

  const handleRemoveEvidence = async (refId: string) => {
    try {
      await apiRemoveEvidence(workspaceId, refId);
      message.success('已移除数据');
      void fetchEvidence();
      onEvidenceChanged();
    } catch {
      message.error('移除失败');
    }
  };

  const handleFreeze = async () => {
    setFreezing(true);
    try {
      await apiFreezeSnapshot(workspaceId);
      message.success('快照已冻结');
      void fetchSnapshots();
    } catch (err) {
      const msg = err instanceof Error ? err.message : '冻结失败';
      message.error(msg);
    } finally {
      setFreezing(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* 添加数据按钮 */}
      <Button
        type="dashed"
        icon={<PlusOutlined />}
        onClick={handleOpenModal}
      >
        添加数据
      </Button>

      {/* 已选数据列表 */}
      <div>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>
          数据集（{evidenceCount}）
        </div>
        {loadingEvidence ? (
          <Spin size="small" />
        ) : evidenceRefs.length === 0 ? (
          <div style={{ color: 'var(--ocean-text-muted)', fontSize: 13 }}>暂无数据引用</div>
        ) : (
          <List
            size="small"
            bordered
            dataSource={evidenceRefs}
            renderItem={(ref) => (
              <List.Item
                actions={[
                  <Button
                    key="remove"
                    size="small"
                    type="link"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => handleRemoveEvidence(ref.ref_id)}
                  />,
                ]}
              >
                <List.Item.Meta
                  title={ref.source_name || ref.source_namespace}
                  description={
                    <span>
                      <Tag color={ref.status === 'active' ? 'green' : 'default'}>
                        {ref.status}
                      </Tag>
                      {ref.source_version && <Tag>{ref.source_version}</Tag>}
                    </span>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </div>

      {/* 冻结快照按钮 */}
      <Button
        type="primary"
        icon={<CameraOutlined />}
        loading={freezing}
        onClick={handleFreeze}
        disabled={evidenceRefs.length === 0}
      >
        冻结快照
      </Button>

      {/* 最新快照信息卡片 */}
      {snapshots.length > 0 && (() => {
        const latest = snapshots[snapshots.length - 1];
        return (
          <div style={{
            marginTop: 8,
            padding: '6px 10px',
            background: 'var(--ocean-bg-subtle)',
            border: '1px solid var(--ocean-border-subtle)',
            borderRadius: 6,
            fontSize: 12,
          }}>
            <div style={{ fontWeight: 600, marginBottom: 2 }}>
              快照 #{latest.snapshot_number}
            </div>
            <div style={{ color: 'var(--ocean-text-muted)' }}>
              {new Date(latest.captured_at).toLocaleString()}
            </div>
            <div style={{ color: 'var(--ocean-text-muted)', fontSize: 11 }}>
              哈希: {latest.content_hash.substring(0, 16)}...
            </div>
          </div>
        );
      })()}

      {/* 树形多选弹窗 */}
      <Modal
        title="添加数据"
        open={modalOpen}
        onOk={handleConfirmAdd}
        onCancel={() => { setModalOpen(false); setSelectedFactIds([]); setSelectedDerivedIds([]); }}
        confirmLoading={adding}
        okText={`添加 ${(evidenceType === 'fact' ? selectedFactIds.length : selectedDerivedIds.length) > 0 ? `(${evidenceType === 'fact' ? selectedFactIds.length : selectedDerivedIds.length})` : ''}`}
        cancelText="取消"
        width={700}
        styles={{ body: { padding: 0 } }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 280px)' }}>
          {/* 类型筛选 */}
          <div style={{ padding: '12px 16px 8px', borderBottom: '1px solid var(--ocean-border-subtle)' }}>
            <Radio.Group
              value={evidenceType}
              onChange={(e) => setEvidenceType(e.target.value)}
              style={{ marginBottom: 8 }}
            >
              <Radio.Button value="fact">实验事实</Radio.Button>
              <Radio.Button value="derived">衍生数据</Radio.Button>
            </Radio.Group>

            {evidenceType === 'fact' && (
              <>
                <Input
                  prefix={<SearchOutlined />}
                  placeholder="搜索样品名称或任务名称..."
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                  allowClear
                  size="middle"
                />
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
                  <Checkbox
                    checked={allSelected}
                    indeterminate={!allSelected && someSelected}
                    onChange={selectAll}
                  >
                    全选 ({allFilteredFactIds.length} 个样品)
                  </Checkbox>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    已选 {selectedFactIds.length} 个
                  </Text>
                </div>
              </>
            )}

            {evidenceType === 'derived' && (
              <>
                <Input
                  prefix={<SearchOutlined />}
                  placeholder="搜索衍生数据集名称..."
                  onChange={(e) => handleSearchDerived(e.target.value)}
                  allowClear
                  size="middle"
                />
                <Text type="secondary" style={{ fontSize: 12, marginTop: 8, display: 'block' }}>
                  已选 {selectedDerivedIds.length} 个
                </Text>
              </>
            )}
          </div>

          {/* 数据列表区 */}
          <div style={{ flex: 1, overflow: 'auto', padding: '8px 16px' }}>
            {evidenceType === 'fact' ? (
              loadingFacts ? (
                <div style={{ textAlign: 'center', padding: 40 }}>
                  <Spin />
                </div>
              ) : Object.keys(factGroups).length === 0 ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--ocean-text-muted)' }}>
                  <Text type="secondary">暂无数据或未找到匹配的样品</Text>
                </div>
              ) : (
                Object.entries(factGroups).map(([projKey, project]) => {
                  const projectTaskIds = Object.values(project.tasks).flatMap((t) => t.facts.map((f) => f.fact_id));
                  const projectAllSelected = projectTaskIds.every((id) => selectedFactIds.includes(id));
                  const projectSomeSelected = projectTaskIds.some((id) => selectedFactIds.includes(id));
                  return (
                    <div key={projKey} style={{ marginBottom: 4 }}>
                      <div
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          padding: '8px 0', cursor: 'pointer',
                          borderBottom: '1px solid var(--ocean-border-subtle)', userSelect: 'none',
                        }}
                        onClick={() => {
                          setExpandedKeys((prev) => {
                            const next = new Set(prev);
                            if (next.has(projKey)) next.delete(projKey);
                            else next.add(projKey);
                            return next;
                          });
                        }}
                      >
                        <span style={{ fontSize: 10, color: 'var(--ocean-text-muted)', width: 12, display: 'inline-block' }}>
                          {searchText.trim() || expandedKeys.has(projKey) ? '▼' : '▶'}
                        </span>
                        <Checkbox
                          checked={projectAllSelected}
                          indeterminate={!projectAllSelected && projectSomeSelected}
                          onChange={() => toggleGroup(projectTaskIds)}
                          onClick={(e) => e.stopPropagation()}
                        />
                        <Text strong style={{ fontSize: 14 }}>{project.projectName}</Text>
                        <Tag style={{ fontSize: 10, margin: 0 }}>{projectTaskIds.length}</Tag>
                      </div>
                      {(searchText.trim() || expandedKeys.has(projKey)) && (
                        <div style={{ paddingLeft: 24 }}>
                          {Object.entries(project.tasks).map(([taskCode, group]) => {
                            const groupIds = group.facts.map((f) => f.fact_id);
                            const groupAllSelected = groupIds.every((id) => selectedFactIds.includes(id));
                            const groupSomeSelected = groupIds.some((id) => selectedFactIds.includes(id));
                            const taskExpandedKey = `${projKey}::${taskCode}`;
                            const isTaskExpanded = searchText.trim() || expandedKeys.has(taskExpandedKey);
                            return (
                              <div key={taskCode} style={{ marginBottom: 2 }}>
                                <div
                                  style={{
                                    display: 'flex', alignItems: 'center', gap: 8,
                                    padding: '6px 0', cursor: 'pointer', userSelect: 'none',
                                  }}
                                  onClick={() => {
                                    setExpandedKeys((prev) => {
                                      const next = new Set(prev);
                                      if (next.has(taskExpandedKey)) next.delete(taskExpandedKey);
                                      else next.add(taskExpandedKey);
                                      return next;
                                    });
                                  }}
                                >
                                  <span style={{ fontSize: 10, color: 'var(--ocean-text-muted)', width: 12, display: 'inline-block' }}>
                                    {isTaskExpanded ? '▼' : '▶'}
                                  </span>
                                  <Checkbox
                                    checked={groupAllSelected}
                                    indeterminate={!groupAllSelected && groupSomeSelected}
                                    onChange={() => toggleGroup(groupIds)}
                                    onClick={(e) => e.stopPropagation()}
                                  />
                                  <Text style={{ fontSize: 13, fontWeight: 500 }}>{group.taskName}</Text>
                                  <Tag style={{ fontSize: 10, margin: 0 }}>{group.facts.length}</Tag>
                                </div>
                                {isTaskExpanded && (
                                  <div style={{ paddingLeft: 28, paddingTop: 2 }}>
                                    {group.facts.map((f) => (
                                      <div
                                        key={f.fact_id}
                                        style={{
                                          display: 'flex', alignItems: 'center', gap: 8,
                                          padding: '4px 0', cursor: 'pointer', borderRadius: 4,
                                          background: selectedFactIds.includes(f.fact_id) ? 'rgba(22, 134, 174, 0.10)' : 'transparent',
                                        }}
                                        onClick={() => toggleFact(f.fact_id)}
                                      >
                                        <Checkbox
                                          checked={selectedFactIds.includes(f.fact_id)}
                                          onChange={() => toggleFact(f.fact_id)}
                                          onClick={(e) => e.stopPropagation()}
                                        />
                                        <Text style={{ fontSize: 13, fontFamily: 'monospace' }}>{f.subject_id}</Text>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })
              )
            ) : (
              // 衍生数据列表
              loadingDerived ? (
                <div style={{ textAlign: 'center', padding: 40 }}>
                  <Spin />
                </div>
              ) : derivedResults.length === 0 ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--ocean-text-muted)' }}>
                  <Text type="secondary">暂无已确认的衍生数据集</Text>
                </div>
              ) : (
                <List
                  size="small"
                  dataSource={derivedResults}
                  renderItem={(item) => (
                    <List.Item
                      style={{ cursor: 'pointer', background: selectedDerivedIds.includes(item.id) ? 'rgba(22, 134, 174, 0.10)' : 'transparent' }}
                      onClick={() => {
                        setSelectedDerivedIds((prev) =>
                          prev.includes(item.id) ? prev.filter((id) => id !== item.id) : [...prev, item.id]
                        );
                      }}
                    >
                      <List.Item.Meta
                        title={
                          <Space>
                            <Checkbox
                              checked={selectedDerivedIds.includes(item.id)}
                              onClick={(e) => e.stopPropagation()}
                              onChange={() => {
                                setSelectedDerivedIds((prev) =>
                                  prev.includes(item.id) ? prev.filter((id) => id !== item.id) : [...prev, item.id]
                                );
                              }}
                            />
                            <Text>衍生: {item.name}</Text>
                            <Tag>v{item.current_version}</Tag>
                          </Space>
                        }
                        description={
                          <span style={{ fontSize: 12 }}>
                            {item.summary || '无摘要'}
                            {item.tags.length > 0 && ` | 标签: ${item.tags.join(', ')}`}
                          </span>
                        }
                      />
                    </List.Item>
                  )}
                />
              )
            )}
          </div>
        </div>
      </Modal>
    </div>
  );
}
