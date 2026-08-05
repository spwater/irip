/**
 * FactDataModal — 载入实验数据 Modal（三层树：项目→任务→数据）。
 *
 * 从 AssistantPage.tsx 提取。包含搜索栏、全选/分组全选/单选、
 * 按项目→任务三层展开/折叠。
 */

import {
  Checkbox,
  Input,
  Modal,
  Tag,
  Typography,
} from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import type { FactDataModalProps } from '../types';

const { Text } = Typography;

export function FactDataModal(props: FactDataModalProps): JSX.Element {
  const {
    open,
    insertingFact,
    factSearchText,
    setFactSearchText,
    factGroups,
    expandedGroups,
    setExpandedGroups,
    selectedFactIds,
    allFilteredFactIds,
    allSelected,
    someSelected,
    onSelectAll,
    onToggleFact,
    onToggleGroup,
    onOk,
    onCancel,
  } = props;

  return (
    <Modal
      title="载入实验数据"
      open={open}
      onOk={onOk}
      onCancel={onCancel}
      confirmLoading={insertingFact}
      okText={`载入 ${selectedFactIds.length > 0 ? `(${selectedFactIds.length})` : ''}`}
      cancelText="取消"
      width={700}
      styles={{ body: { padding: 0 } }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 280px)' }}>
        {/* 搜索栏 */}
        <div style={{ padding: '12px 16px 8px', borderBottom: '1px solid var(--ocean-border-subtle)' }}>
          <Input
            prefix={<SearchOutlined />}
            placeholder="搜索样品名称或任务名称..."
            value={factSearchText}
            onChange={(e) => setFactSearchText(e.target.value)}
            allowClear
            size="middle"
          />
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
            <Checkbox
              checked={allSelected}
              indeterminate={!allSelected && someSelected}
              onChange={onSelectAll}
            >
              全选 ({allFilteredFactIds.length} 个样品)
            </Checkbox>
            <Text type="secondary" style={{ fontSize: 12 }}>
              已选 {selectedFactIds.length} 个
            </Text>
          </div>
        </div>

        {/* 分组列表：三层树 项目→任务→数据 */}
        <div style={{ flex: 1, overflow: 'auto', padding: '8px 16px' }}>
          {Object.keys(factGroups).length === 0 ? (
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
                  {/* 项目分组标题 */}
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: '8px 0',
                      cursor: 'pointer',
                      borderBottom: '1px solid var(--ocean-border-subtle)',
                      userSelect: 'none',
                    }}
                    onClick={() => {
                      setExpandedGroups((prev) => {
                        const next = new Set(prev);
                        if (next.has(projKey)) next.delete(projKey);
                        else next.add(projKey);
                        return next;
                      });
                    }}
                  >
                    <span style={{ fontSize: 10, color: 'var(--ocean-text-muted)', width: 12, display: 'inline-block' }}>
                      {factSearchText.trim() || expandedGroups.has(projKey) ? '▼' : '▶'}
                    </span>
                    <Checkbox
                      checked={projectAllSelected}
                      indeterminate={!projectAllSelected && projectSomeSelected}
                      onChange={() => onToggleGroup(projectTaskIds)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <Text strong style={{ fontSize: 14 }}>{project.projectName}</Text>
                    <Tag style={{ fontSize: 10, margin: 0 }}>{projectTaskIds.length}</Tag>
                  </div>
                  {/* 任务列表 */}
                  {(factSearchText.trim() || expandedGroups.has(projKey)) && (
                    <div style={{ paddingLeft: 24 }}>
                      {Object.entries(project.tasks).map(([taskCode, group]) => {
                        const groupIds = group.facts.map((f) => f.fact_id);
                        const groupAllSelected = groupIds.every((id) => selectedFactIds.includes(id));
                        const groupSomeSelected = groupIds.some((id) => selectedFactIds.includes(id));
                        const taskExpandedKey = `${projKey}::${taskCode}`;
                        const isTaskExpanded = factSearchText.trim() || expandedGroups.has(taskExpandedKey);
                        return (
                          <div key={taskCode} style={{ marginBottom: 2 }}>
                            <div
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 8,
                                padding: '6px 0',
                                cursor: 'pointer',
                                userSelect: 'none',
                              }}
                              onClick={() => {
                                setExpandedGroups((prev) => {
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
                                onChange={() => onToggleGroup(groupIds)}
                                onClick={(e) => e.stopPropagation()}
                              />
                              <Text style={{ fontSize: 13, fontWeight: 500 }}>{group.taskName}</Text>
                              <Tag style={{ fontSize: 10, margin: 0 }}>{group.facts.length}</Tag>
                            </div>
                            {/* 样品列表 */}
                            {isTaskExpanded && (
                              <div style={{ paddingLeft: 28, paddingTop: 2 }}>
                                {group.facts.map((f) => (
                                  <div
                                    key={f.fact_id}
                                    style={{
                                      display: 'flex',
                                      alignItems: 'center',
                                      gap: 8,
                                      padding: '4px 0',
                                      cursor: 'pointer',
                                      borderRadius: 4,
                                      background: selectedFactIds.includes(f.fact_id) ? 'rgba(22, 134, 174, 0.10)' : 'transparent',
                                    }}
                                    onClick={() => onToggleFact(f.fact_id)}
                                  >
                                    <Checkbox
                                      checked={selectedFactIds.includes(f.fact_id)}
                                      onChange={() => onToggleFact(f.fact_id)}
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
          )}
        </div>
      </div>
    </Modal>
  );
}

export default FactDataModal;
