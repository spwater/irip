/**
 * InsightModifyModal — Insight 修改面板
 *
 * AI 原稿只读 + 6 字段编辑 + 修改原因
 */
import { useState, useEffect } from 'react';
import { Modal, Input, Select, Button, Space, Tag, Typography, message } from 'antd';
import { PlusOutlined, MinusCircleOutlined } from '@ant-design/icons';
import { apiModifyCandidate, type CandidateProduct } from '@/api/researchProducts';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

const SOURCE_OPTIONS = [
  { value: 'experimental_data', label: '实验数据' },
  { value: 'knowledge_base', label: '知识库' },
  { value: 'model_inference', label: '模型推测' },
];

export type InsightModifyModalProps = {
  workspaceId: string;
  runId: string;
  candidate: CandidateProduct;
  onClose: () => void;
  onSuccess: () => void;
};

export function InsightModifyModal({
  workspaceId,
  runId,
  candidate,
  onClose,
  onSuccess,
}: InsightModifyModalProps): JSX.Element {
  const preview = candidate.preview_data as Record<string, unknown>;
  const aiRawText = (preview?.ai_raw_text as string) ?? '';
  const originalConclusion = (preview?.conclusion as string) ?? '';
  const originalScope = (preview?.scope as string) ?? '';
  const originalEvidenceRefs = (preview?.evidence_refs as Array<Record<string, unknown>>) ?? [];
  const originalMethodRefs = (preview?.method_refs as Array<Record<string, unknown>>) ?? [];
  const originalConfidence = (preview?.confidence_level as string) ?? '';
  const originalLimitations = (preview?.limitations as string) ?? '';
  const originalLabel = (preview?.evidence_source_label as string) ?? 'model_inference';

  const [conclusion, setConclusion] = useState(originalConclusion);
  const [scope, setScope] = useState(originalScope);
  const [evidenceRefs, setEvidenceRefs] = useState(originalEvidenceRefs);
  const [methodRefs, setMethodRefs] = useState(originalMethodRefs);
  const [confidenceLevel, setConfidenceLevel] = useState(originalConfidence);
  const [limitations, setLimitations] = useState(originalLimitations);
  const [evidenceSourceLabel, setEvidenceSourceLabel] = useState(originalLabel);
  const [modificationNote, setModificationNote] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setConclusion(originalConclusion);
    setScope(originalScope);
    setEvidenceRefs(originalEvidenceRefs);
    setMethodRefs(originalMethodRefs);
    setConfidenceLevel(originalConfidence);
    setLimitations(originalLimitations);
    setEvidenceSourceLabel(originalLabel);
  }, [originalConclusion, originalScope, originalEvidenceRefs, originalMethodRefs, originalConfidence, originalLimitations, originalLabel]);

  const handleAddEvidenceRef = () => {
    setEvidenceRefs([...evidenceRefs, { type: 'dataset', name: '', version: 1 }]);
  };

  const handleRemoveEvidenceRef = (idx: number) => {
    setEvidenceRefs(evidenceRefs.filter((_, i) => i !== idx));
  };

  const handleAddMethodRef = () => {
    setMethodRefs([...methodRefs, { run_id: '', step_key: '' }]);
  };

  const handleRemoveMethodRef = (idx: number) => {
    setMethodRefs(methodRefs.filter((_, i) => i !== idx));
  };

  const handleSubmit = async () => {
    if (!modificationNote.trim()) {
      message.warning('修改原因为必填');
      return;
    }
    setSubmitting(true);
    try {
      await apiModifyCandidate(workspaceId, runId, candidate.candidate_id, {
        conclusion,
        scope,
        evidence_refs: evidenceRefs,
        method_refs: methodRefs,
        confidence_level: confidenceLevel,
        limitations,
        evidence_source_label: evidenceSourceLabel,
        modification_note: modificationNote,
      });
      message.success('修改成功');
      onSuccess();
    } catch {
      message.error('修改失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="修改 Insight 候选"
      open={true}
      onCancel={onClose}
      width={680}
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button key="confirm" type="primary" loading={submitting} onClick={handleSubmit}>
          确认修改
        </Button>,
      ]}
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {/* AI 原稿（只读） */}
        <div>
          <Text type="secondary">AI 原稿 (只读):</Text>
          <Paragraph
            style={{
              background: '#f5f5f5',
              padding: 12,
              borderRadius: 4,
              marginTop: 4,
              whiteSpace: 'pre-wrap',
              fontSize: 13,
            }}
          >
            {aiRawText}
          </Paragraph>
        </div>

        {/* 证据来源 */}
        <div>
          <Text>证据来源:</Text>
          <Select
            value={evidenceSourceLabel}
            onChange={setEvidenceSourceLabel}
            options={SOURCE_OPTIONS}
            style={{ width: '100%', marginTop: 4 }}
          />
        </div>

        {/* 结论 */}
        <div>
          <Text>结论 *:</Text>
          <TextArea
            value={conclusion}
            onChange={(e) => setConclusion(e.target.value)}
            rows={2}
            style={{ marginTop: 4 }}
          />
        </div>

        {/* 适用范围 */}
        <div>
          <Text>适用范围 *:</Text>
          <TextArea
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            rows={2}
            style={{ marginTop: 4 }}
          />
        </div>

        {/* 证据引用 */}
        <div>
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text>证据引用 *:</Text>
            <Button size="small" icon={<PlusOutlined />} onClick={handleAddEvidenceRef}>
              添加
            </Button>
          </Space>
          {evidenceRefs.map((ref, idx) => (
            <Space key={`ev-${idx}`} style={{ display: 'flex', marginTop: 4 }}>
              <Tag>{String(ref.name ?? ref.type ?? '')}</Tag>
              <Button
                size="small"
                type="link"
                danger
                icon={<MinusCircleOutlined />}
                onClick={() => handleRemoveEvidenceRef(idx)}
              />
            </Space>
          ))}
        </div>

        {/* 方法引用 */}
        <div>
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text>方法引用 *:</Text>
            <Button size="small" icon={<PlusOutlined />} onClick={handleAddMethodRef}>
              添加
            </Button>
          </Space>
          {methodRefs.map((ref, idx) => (
            <Space key={`mth-${idx}`} style={{ display: 'flex', marginTop: 4 }}>
              <Tag>{String(ref.step_key ?? ref.run_id ?? '')}</Tag>
              <Button
                size="small"
                type="link"
                danger
                icon={<MinusCircleOutlined />}
                onClick={() => handleRemoveMethodRef(idx)}
              />
            </Space>
          ))}
        </div>

        {/* 置信度 */}
        <div>
          <Text>置信说明 *:</Text>
          <Input
            value={confidenceLevel}
            onChange={(e) => setConfidenceLevel(e.target.value)}
            placeholder="如: 高 / 中 / 低 或说明文本"
            style={{ marginTop: 4 }}
          />
        </div>

        {/* 限制条件 */}
        <div>
          <Text>限制条件 *:</Text>
          <TextArea
            value={limitations}
            onChange={(e) => setLimitations(e.target.value)}
            rows={2}
            style={{ marginTop: 4 }}
          />
        </div>

        {/* 修改原因 */}
        <div>
          <Text>修改原因 *:</Text>
          <TextArea
            value={modificationNote}
            onChange={(e) => setModificationNote(e.target.value)}
            rows={2}
            placeholder="说明修改原因"
            style={{ marginTop: 4 }}
          />
        </div>
      </Space>
    </Modal>
  );
}
