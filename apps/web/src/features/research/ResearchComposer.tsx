/**
 * ResearchComposer — 人工提问 + 背景文件 + 结论选择 + 创建 Turn。
 *
 * 用户在这里输入研究问题，可选上传背景文件作为推荐上下文，
 * 选择历史结论作为上下文，然后创建一个新的研究轮次。
 */
import { useState, useEffect, useRef } from 'react';
import { Button, Input, Typography, message, Tag } from 'antd';
import { PaperClipOutlined } from '@ant-design/icons';
import { createTurn } from '@/api/researchTimeline';
import { http } from '@/api/client';

const { Text } = Typography;
const { TextArea } = Input;

interface Props {
  workspaceId: string;
  snapshotId: string;
  selectedRevisionIds: string[];
  initialQuestion?: string;
  onTurnCreated: (turnId: string) => void;
}

export function ResearchComposer({ workspaceId, snapshotId, selectedRevisionIds, initialQuestion, onTurnCreated }: Props) {
  const [question, setQuestion] = useState(initialQuestion || '');
  const [loading, setLoading] = useState(false);
  const [backgroundFiles, setBackgroundFiles] = useState<File[]>([]);
  const [extractedText, setExtractedText] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (initialQuestion) {
      setQuestion(initialQuestion);
    }
  }, [initialQuestion]);

  const handleFileSelect = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const newFiles = Array.from(files);
    setBackgroundFiles((prev) => [...prev, ...newFiles]);

    // Extract text from each file
    for (const file of newFiles) {
      try {
        const formData = new FormData();
        formData.append('file', file);
        const res = await http.post<{ text?: string; error?: string }>(
          '/research/extract-text',
          formData,
          { headers: { 'Content-Type': 'multipart/form-data' } },
        );
        if (res.data.text) {
          setExtractedText((prev) => prev + (prev ? '\n\n' : '') + res.data.text!);
        }
      } catch {
        message.warning(`文件 ${file.name} 解析失败`);
      }
    }
  };

  const removeFile = (idx: number) => {
    setBackgroundFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleCreate = async () => {
    if (!question.trim()) {
      message.warning('请输入研究问题');
      return;
    }
    setLoading(true);
    try {
      const ref = await createTurn(workspaceId, {
        question_text: question.trim(),
        evidence_snapshot_id: snapshotId,
        selected_conclusion_revision_ids: selectedRevisionIds,
      });
      message.success(`研究轮次 #${ref.turn_number} 已创建，正在启动分析...`);

      // Refresh timeline immediately — don't wait for analysis to finish
      setQuestion('');
      setBackgroundFiles([]);
      setExtractedText('');
      onTurnCreated(ref.turn_id);

      // Auto-start analysis in background
      try {
        await http.post(
          `/research/workspaces/${workspaceId}/turns/${ref.turn_id}/analyze`,
          { background_text: extractedText || undefined },
        );
      } catch {
        // non-fatal — user can retry from timeline
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '创建失败';
      message.error(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 16, border: '1px solid var(--ocean-border-subtle)', borderRadius: 8 }}>
      <Text strong style={{ display: 'block', marginBottom: 8 }}>
        {'提出研究问题'}
      </Text>

      {/* 背景文件区 */}
      <div style={{ marginBottom: 8 }}>
        {backgroundFiles.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            {backgroundFiles.map((file, idx) => (
              <Tag
                key={idx}
                closable
                onClose={() => removeFile(idx)}
                style={{ marginBottom: 4 }}
                icon={<PaperClipOutlined />}
              >
                {file.name}
              </Tag>
            ))}
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.doc,.docx,.txt,.md,.csv,.xlsx,.xls"
          style={{ display: 'none' }}
          onChange={(e) => {
            handleFileSelect(e.target.files);
            e.target.value = '';
          }}
        />
        <Button
          size="small"
          type="dashed"
          icon={<PaperClipOutlined />}
          onClick={() => fileInputRef.current?.click()}
        >
          {'添加背景文件'}
        </Button>
        {backgroundFiles.length > 0 && (
          <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
            {`已上传 ${backgroundFiles.length} 个文件，将作为推荐上下文`}
          </Text>
        )}
      </div>

      <TextArea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="输入你想研究的问题..."
        autoSize={{ minRows: 2 }}
        style={{ marginBottom: 8 }}
      />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {selectedRevisionIds.length > 0
            ? `已选 ${selectedRevisionIds.length} 条历史结论作为上下文`
            : '未选择历史结论（可以不选）'}
        </Text>
        <Button
          type="primary"
          size="small"
          loading={loading}
          disabled={!question.trim()}
          onClick={handleCreate}
        >
          {'创建研究轮次'}
        </Button>
      </div>
    </div>
  );
}
