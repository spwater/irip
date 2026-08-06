/**
 * InsightDetailView — Insight 详情组件
 *
 * 展示结构化字段 + 证据来源标签 + AI 原稿 + 修改记录 + 版本历史
 */
import { useState, useEffect, useCallback } from 'react';
import { Card, Spin, Tag, Typography, Space, Button, Input, List, message } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { EditOutlined } from '@ant-design/icons';
import {
  apiGetInsight,
  apiListInsightVersions,
  apiUpdateInsightMetadata,
  type InsightDetail,
  type InsightVersion,
} from '@/api/researchProducts';
import { KnowledgeReferenceList } from './KnowledgeReferenceList';

const { Text } = Typography;

const SOURCE_LABEL_MAP: Record<string, { label: string; color: string }> = {
  experimental_data: { label: '实验数据', color: 'blue' },
  knowledge_base: { label: '知识库', color: 'purple' },
  model_inference: { label: '模型推测', color: 'orange' },
};

export type InsightDetailViewProps = {
  workspaceId: string;
  insightId: string;
};

export function InsightDetailView({ workspaceId, insightId }: InsightDetailViewProps): JSX.Element {
  const [detail, setDetail] = useState<InsightDetail | null>(null);
  const [versions, setVersions] = useState<InsightVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState('');

  const fetchDetail = useCallback(async () => {
    setLoading(true);
    try {
      const [detailRes, versionRes] = await Promise.all([
        apiGetInsight(workspaceId, insightId),
        apiListInsightVersions(workspaceId, insightId),
      ]);
      setDetail(detailRes);
      setVersions(versionRes.items);
      setEditName(detailRes.name);
    } catch {
      message.error('加载 Insight 详情失败');
    } finally {
      setLoading(false);
    }
  }, [workspaceId, insightId]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  const handleSaveEdit = async () => {
    try {
      await apiUpdateInsightMetadata(workspaceId, insightId, { name: editName });
      message.success('已保存');
      setEditing(false);
      void fetchDetail();
    } catch {
      message.error('保存失败');
    }
  };

  if (loading || !detail) {
    return (
      <div style={{ textAlign: 'center', padding: 40 }}>
        <Spin />
      </div>
    );
  }

  const versionData = detail.current_version_data as Record<string, unknown> | null;
  const conclusion = (versionData?.conclusion as string) ?? '';
  const scope = (versionData?.scope as string) ?? '';
  const evidenceRefs = (versionData?.evidence_refs as Array<Record<string, unknown>>) ?? [];
  const methodRefs = (versionData?.method_refs as Array<Record<string, unknown>>) ?? [];
  const confidenceLevel = (versionData?.confidence_level as string) ?? '';
  const limitations = (versionData?.limitations as string) ?? '';
  const evidenceSourceLabel = (versionData?.evidence_source_label as string) ?? 'model_inference';
  const aiOriginalText = (versionData?.ai_original_text as string) ?? '';
  const isModified = (versionData?.is_modified as boolean) ?? false;
  const modificationNote = (versionData?.modification_note as string) ?? '';

  const labelInfo = SOURCE_LABEL_MAP[evidenceSourceLabel] ?? SOURCE_LABEL_MAP.model_inference;

  return (
    <div>
      {/* 头部 */}
      <Card size="small" style={{ marginBottom: 12 }}>
        {editing ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="名称" />
            <Space>
              <Button size="small" type="primary" onClick={handleSaveEdit}>保存</Button>
              <Button size="small" onClick={() => setEditing(false)}>取消</Button>
            </Space>
          </Space>
        ) : (
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space>
              <Text strong style={{ fontSize: 16 }}>{detail.name}</Text>
              <Tag>{detail.status}</Tag>
              <Tag color="blue">v{detail.current_version}</Tag>
              <Tag color={labelInfo.color}>{labelInfo.label}</Tag>
            </Space>
            <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(true)}>编辑</Button>
          </Space>
        )}
      </Card>

      {/* 结构化字段 */}
      <Card size="small" title="结构化字段" style={{ marginBottom: 12 }}>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <div>
            <Text type="secondary" style={{ fontSize: 11 }}>结论:</Text>
            <div className="research-markdown" style={{ fontSize: 13, marginTop: 2 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{conclusion}</ReactMarkdown>
            </div>
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 11 }}>适用范围:</Text>
            <div className="research-markdown" style={{ fontSize: 13, marginTop: 2 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{scope}</ReactMarkdown>
            </div>
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 11 }}>置信度:</Text>
            <Text style={{ fontSize: 13 }}> {confidenceLevel}</Text>
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 11 }}>限制条件:</Text>
            <div className="research-markdown" style={{ fontSize: 13, marginTop: 2 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{limitations}</ReactMarkdown>
            </div>
          </div>
          {evidenceRefs.length > 0 && (
            <div>
              <Text type="secondary" style={{ fontSize: 11 }}>证据引用:</Text>
              <Space size="small" wrap style={{ marginTop: 4 }}>
                {evidenceRefs.map((ref, i) => (
                  <Tag key={i}>{String(ref.name ?? ref.id ?? '')}</Tag>
                ))}
              </Space>
            </div>
          )}
          {methodRefs.length > 0 && (
            <div>
              <Text type="secondary" style={{ fontSize: 11 }}>方法引用:</Text>
              <Space size="small" wrap style={{ marginTop: 4 }}>
                {methodRefs.map((ref, i) => (
                  <Tag key={i}>{String(ref.step_key ?? ref.run_id ?? '')}</Tag>
                ))}
              </Space>
            </div>
          )}
        </Space>
      </Card>

      {/* AI 原稿 + 修改记录 */}
      {(aiOriginalText || isModified) && (
        <Card size="small" title="AI 原稿与修改记录" style={{ marginBottom: 12 }}>
          {aiOriginalText && (
            <div style={{ marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 11 }}>AI 原稿:</Text>
              <div
                className="research-markdown"
                style={{
                  background: '#f5f5f5',
                  padding: 12,
                  borderRadius: 4,
                  marginTop: 4,
                  fontSize: 13,
                }}
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{aiOriginalText}</ReactMarkdown>
              </div>
            </div>
          )}
          {isModified && (
            <div>
              <Tag color="orange">已修改</Tag>
              {modificationNote && (
                <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                  修改原因: {modificationNote}
                </Text>
              )}
            </div>
          )}
        </Card>
      )}

      {/* 版本历史 */}
      {versions.length > 0 && (
        <Card size="small" title="版本历史" style={{ marginBottom: 12 }}>
          <List
            size="small"
            dataSource={versions}
            renderItem={(v) => (
              <List.Item>
                <Space>
                  <Tag>v{v.version_number}</Tag>
                  {v.is_modified && <Tag color="orange">已修改</Tag>}
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {new Date(v.created_at).toLocaleString()}
                  </Text>
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}

      {/* 知识库引用快照（阶段 5 新增） */}
      {/* 仅当证据来源为知识库时展示，否则也尝试展示（后端会返回空列表） */}
      <KnowledgeReferenceList
        insightId={insightId}
        hasManagePermission={false}
      />
    </div>
  );
}
