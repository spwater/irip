/**
 * CandidateInsightCard — 候选 Insight 卡片
 *
 * 6 个结构化字段 + 证据来源标签 + 接受/修改/拒绝按钮
 */
import { useState } from 'react';
import { Card, Tag, Button, Typography, Space, Collapse, Popconfirm, Input, message } from 'antd';
import {
  BulbOutlined,
  CheckOutlined,
  EditOutlined,
  CloseOutlined,
} from '@ant-design/icons';
import type { CandidateProduct } from '@/api/researchProducts';

const { Text, Paragraph } = Typography;

const SOURCE_LABEL_MAP: Record<string, { label: string; color: string }> = {
  experimental_data: { label: '实验数据', color: 'blue' },
  knowledge_base: { label: '知识库', color: 'purple' },
  model_inference: { label: '模型推测', color: 'orange' },
};

export type CandidateInsightCardProps = {
  workspaceId: string;
  runId: string;
  candidate: CandidateProduct;
  onAccept: (candidateId: string) => void;
  onModify: (candidate: CandidateProduct) => void;
  onReject: (candidateId: string, reason?: string) => void;
};

export function CandidateInsightCard({
  workspaceId,
  runId,
  candidate,
  onAccept,
  onModify,
  onReject,
}: CandidateInsightCardProps): JSX.Element {
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  const preview = candidate.preview_data as Record<string, unknown>;
  const conclusion = (preview?.conclusion as string) ?? '';
  const scope = (preview?.scope as string) ?? '';
  const evidenceRefs = (preview?.evidence_refs as Array<Record<string, unknown>>) ?? [];
  const methodRefs = (preview?.method_refs as Array<Record<string, unknown>>) ?? [];
  const confidenceLevel = (preview?.confidence_level as string) ?? '';
  const limitations = (preview?.limitations as string) ?? '';
  const evidenceSourceLabel = (preview?.evidence_source_label as string) ?? 'model_inference';
  const aiRawText = (preview?.ai_raw_text as string) ?? '';

  const labelInfo = SOURCE_LABEL_MAP[evidenceSourceLabel] ?? SOURCE_LABEL_MAP.model_inference;

  const handleReject = () => {
    setRejecting(false);
    onReject(candidate.candidate_id, rejectReason || undefined);
  };

  return (
    <Card
      size="small"
      style={{ marginBottom: 8 }}
      actions={[
        <Button
          key="accept"
          type="primary"
          size="small"
          icon={<CheckOutlined />}
          onClick={() => onAccept(candidate.candidate_id)}
        >
          接受
        </Button>,
        <Button
          key="modify"
          size="small"
          icon={<EditOutlined />}
          onClick={() => onModify(candidate)}
        >
          修改
        </Button>,
        <Popconfirm
          key="reject"
          title="拒绝此候选"
          description={
            <Input.TextArea
              placeholder="拒绝原因（可选）"
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              rows={2}
              style={{ marginTop: 8 }}
            />
          }
          onConfirm={handleReject}
          onCancel={() => setRejecting(false)}
          okText="确认拒绝"
          cancelText="取消"
        >
          <Button size="small" danger icon={<CloseOutlined />}>
            拒绝
          </Button>
        </Popconfirm>,
      ]}
    >
      <Space direction="vertical" size="small" style={{ width: '100%' }}>
        {/* 证据来源标签 */}
        <Tag color={labelInfo.color}>{labelInfo.label}</Tag>

        {/* 结论 */}
        <div>
          <Text type="secondary" style={{ fontSize: 11 }}>结论:</Text>
          <Paragraph style={{ margin: 0, fontSize: 13 }}>{conclusion}</Paragraph>
        </div>

        {/* 适用范围 */}
        <div>
          <Text type="secondary" style={{ fontSize: 11 }}>适用范围:</Text>
          <Paragraph style={{ margin: 0, fontSize: 13 }}>{scope}</Paragraph>
        </div>

        {/* 置信度 + 限制 */}
        <Space size="small">
          <Tag style={{ fontSize: 10 }}>置信度: {confidenceLevel}</Tag>
        </Space>
        {limitations && (
          <Text type="secondary" style={{ fontSize: 11 }}>
            限制: {limitations}
          </Text>
        )}

        {/* 证据引用 + 方法引用 */}
        {(evidenceRefs.length > 0 || methodRefs.length > 0) && (
          <Space size="small" wrap>
            {evidenceRefs.map((ref, i) => (
              <Tag key={`ev-${i}`} style={{ fontSize: 10 }}>
                证据: {String(ref.name ?? ref.id ?? '')}
              </Tag>
            ))}
            {methodRefs.map((ref, i) => (
              <Tag key={`mth-${i}`} style={{ fontSize: 10 }}>
                方法: {String(ref.step_key ?? ref.run_id ?? '')}
              </Tag>
            ))}
          </Space>
        )}

        {/* AI 原稿（可展开） */}
        {aiRawText && (
          <Collapse
            size="small"
            ghost
            items={[
              {
                key: 'ai_raw',
                label: <Text type="secondary" style={{ fontSize: 11 }}>AI 原稿</Text>,
                children: (
                  <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>
                    {aiRawText}
                  </Text>
                ),
              },
            ]}
          />
        )}
      </Space>
    </Card>
  );
}
