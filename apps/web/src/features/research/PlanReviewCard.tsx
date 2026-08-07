/**
 * PlanReviewCard — 分析建议卡片
 *
 * 功能：
 * - 默认收起，点击展开查看完整建议（Markdown 渲染）
 * - 编辑模式：用户可修改建议文本
 * - "执行分析"按钮放在标题栏 extra 区（不被内容挤掉）
 */

import { useState } from 'react';
import { Card, Typography, Space, Button, Input, Spin, message } from 'antd';
import {
  BulbOutlined,
  DownOutlined,
  UpOutlined,
  EditOutlined,
  SaveOutlined,
  CloseOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { PlanDetail } from '../../api/research';
import { apiAnalyzeData } from '../../api/research';

export type PlanReviewCardProps = {
  plan: PlanDetail;
  workspaceId: string;
  snapshotId?: string;
  onAnalysisComplete?: (result: { analysis_result: string; data_context?: string }) => void;
  onConfirm?: () => void;
  onAdjust?: () => void;
};

export function PlanReviewCard({
  plan,
  workspaceId,
  snapshotId,
  onAnalysisComplete,
}: PlanReviewCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState('');
  const [executing, setExecuting] = useState(false);

  const steps = plan.dag_structure?.steps || [];
  const originalAdvice = steps.length > 0 ? (steps[0].expected_output || steps[0].question || '') : '';
  const currentAdvice = editText || originalAdvice;
  const preview = currentAdvice.slice(0, 120) + (currentAdvice.length > 120 ? '...' : '');

  const handleEnterEdit = () => {
    setEditText(originalAdvice);
    setEditing(true);
    setExpanded(true);
  };

  const handleSaveEdit = () => {
    setEditing(false);
    message.success('建议已编辑');
  };

  const handleCancelEdit = () => {
    setEditing(false);
    setEditText('');
  };

  const handleExecute = async () => {
    if (!snapshotId) return;
    setExecuting(true);
    try {
      const result = await apiAnalyzeData(
        workspaceId,
        plan.plan_id,
        snapshotId,
        editText || undefined,
      );
      message.success('分析完成');
      if (onAnalysisComplete) {
        onAnalysisComplete(result);
      }
    } catch {
      message.error('分析执行失败');
    } finally {
      setExecuting(false);
    }
  };

  const extraContent = (
    <Space size="small">
      {!editing && (
        <>
          <Button
            size="small"
            type="text"
            icon={<EditOutlined />}
            onClick={handleEnterEdit}
          >
            编辑
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={executing}
            onClick={handleExecute}
          >
            {executing ? '分析中' : '执行分析'}
          </Button>
        </>
      )}
    </Space>
  );

  return (
    <Card
      size="small"
      style={{ marginBottom: 12 }}
      title={
        <Space
          onClick={() => !editing && setExpanded(!expanded)}
          style={{ cursor: editing ? 'default' : 'pointer' }}
        >
          <BulbOutlined style={{ color: '#1890ff' }} />
          <Typography.Text strong>分析建议</Typography.Text>
          {!editing && (
            expanded
              ? <UpOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />
              : <DownOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />
          )}
        </Space>
      }
      extra={extraContent}
    >
      {editing ? (
        <>
          <Input.TextArea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            rows={12}
            style={{ fontSize: 13, lineHeight: 1.6 }}
            placeholder="编辑分析建议..."
          />
          <Space style={{ marginTop: 8 }}>
            <Button size="small" type="primary" icon={<SaveOutlined />} onClick={handleSaveEdit}>
              保存
            </Button>
            <Button size="small" icon={<CloseOutlined />} onClick={handleCancelEdit}>
              取消
            </Button>
          </Space>
        </>
      ) : expanded ? (
        <>
          <div className="research-markdown" style={{ fontSize: 14, lineHeight: 1.8 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{currentAdvice}</ReactMarkdown>
          </div>
          <Button
            type="link"
            size="small"
            icon={<UpOutlined />}
            onClick={() => setExpanded(false)}
            style={{ padding: '4px 0', marginTop: 8 }}
          >
            收起
          </Button>
        </>
      ) : (
        <Typography.Text
          type="secondary"
          style={{ fontSize: 12, cursor: 'pointer' }}
          onClick={() => setExpanded(true)}
        >
          {preview}
        </Typography.Text>
      )}
      {executing && (
        <div style={{ textAlign: 'center', padding: '12px 0' }}>
          <Spin tip="AI 正在分析数据并生成图表..." />
        </div>
      )}
    </Card>
  );
}
