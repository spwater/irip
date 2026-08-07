/**
 * PlanReviewCard — 分析计划审查卡片
 *
 * 功能：
 * - 显示计划版本号 + 步骤数 + 步骤摘要
 * - 覆盖声明预览（分析模式 + 数据覆盖率 + LLM 阅读率）
 * - draft 状态：显示「确认计划」按钮
 * - confirmed 状态：显示「已确认计划 v{version}」状态条
 * - 分析建议编辑 + 执行分析（确认后仍可见）
 */

import { useState } from 'react';
import { Card, Typography, Space, Button, Input, Spin, message, Tag } from 'antd';
import {
  BulbOutlined,
  DownOutlined,
  UpOutlined,
  EditOutlined,
  SaveOutlined,
  CloseOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { PlanDetail } from '../../api/research';
import { apiAnalyzeData } from '../../api/research';

const MODE_LABELS: Record<string, string> = {
  full_compute: '全量计算',
  chunked_full_scan: '分块全量扫描',
  direct_full_context: '直接全量上下文',
  retrieval: '检索探索',
  mixed: '混合分析',
};

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
  onConfirm,
  onAdjust,
}: PlanReviewCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState('');
  const [executing, setExecuting] = useState(false);

  const steps = plan.dag_structure?.steps || [];
  const originalAdvice = steps.length > 0 ? (steps[0].expected_output || steps[0].question || '') : '';
  const currentAdvice = editText || originalAdvice;
  const preview = currentAdvice.slice(0, 120) + (currentAdvice.length > 120 ? '...' : '');

  const isConfirmed = plan.status === 'confirmed';

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

  const handleConfirm = () => {
    if (onConfirm) {
      Promise.resolve(onConfirm());
    }
  };

  const coverage = plan.coverage_declaration;
  const modeLabel = coverage ? (MODE_LABELS[coverage.analysis_mode] || coverage.analysis_mode) : '';
  const dataCoveragePct = coverage ? Math.round(coverage.data_coverage_rate * 100) : 0;
  const llmReadPct = coverage ? Math.round(coverage.llm_read_rate * 100) : 0;

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
          <Typography.Text strong>分析计划 v{plan.version_number}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {steps.length} 步
          </Typography.Text>
          {!editing && (
            expanded
              ? <UpOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />
              : <DownOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />
          )}
        </Space>
      }
      extra={extraContent}
    >
      {/* Confirmed status bar */}
      {isConfirmed && (
        <div style={{ marginBottom: 12 }}>
          <Tag icon={<CheckCircleOutlined />} color="success">
            已确认计划 v{plan.version_number}
          </Tag>
        </div>
      )}

      {/* Step summaries */}
      <div style={{ marginBottom: 12 }}>
        {steps.map((step) => (
          <div
            key={step.step_key}
            style={{
              padding: '4px 0',
              borderBottom: '1px solid #f0f0f0',
              fontSize: 13,
            }}
          >
            <Typography.Text strong>{step.step_key}</Typography.Text>
            <span style={{ marginLeft: 8, color: '#8c8c8c' }}>
              {step.question}
            </span>
          </div>
        ))}
      </div>

      {/* Coverage declaration preview */}
      {coverage && (
        <div
          style={{
            padding: '8px 12px',
            background: '#f5f5f5',
            borderRadius: 6,
            marginBottom: 12,
            fontSize: 13,
            color: '#595959',
          }}
        >
          {modeLabel} | 数据覆盖率 {dataCoveragePct}% | LLM 阅读率 {llmReadPct}%
        </div>
      )}

      {/* Confirm button for draft status */}
      {!isConfirmed && (
        <Space style={{ marginBottom: 12 }}>
          <Button type="primary" onClick={handleConfirm}>
            确认计划
          </Button>
          {onAdjust && (
            <Button size="small" onClick={onAdjust}>
              调整计划
            </Button>
          )}
        </Space>
      )}

      {/* Advice section */}
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
