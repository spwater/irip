/**
 * ResearchCanvas — 研究画布（中栏）
 *
 * 阶段 2 增加内容：
 * - 分析计划区：DAG 步骤线性列表 + 确认/调整按钮
 * - Run 进度区：RunProgressPanel 组件嵌入
 * - 覆盖声明条（与右栏同步）
 * - 候选输出预览区（已完成步骤输出缩略卡片）
 * - 排队时显示 QueueStatus 组件
 *
 * 保留阶段 1 内容：主研究问题 + 子问题 + 数据集状态
 */

import { useState, useEffect, useCallback } from 'react';
import { Card, Tag, Space, Button, Empty, Typography, Modal, Input, message } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChartBlock } from '../assistant/message-thread/components/ChartBlock';
import { ChartRefBlock } from '../assistant/ChartRefBlock';
import {
  PlayCircleOutlined,
  EditOutlined,
  PlusOutlined,
  UpOutlined,
  DownOutlined,
  BulbOutlined,
  BarChartOutlined,
} from '@ant-design/icons';
import {
  apiGeneratePlan,
  apiUpdateQuestion,
  type PlanDetail,
  type RunProgress,
  type InsightCandidate,
  type WorkspaceDetail as WorkspaceDetailType,
} from '../../api/research';
import { PlanReviewCard } from './PlanReviewCard';

export type ResearchCanvasProps = {
  workspaceId: string;
  detail: WorkspaceDetailType;
  onQuestionUpdated?: () => void;
  insightCandidate: InsightCandidate | null;
  insightCandidateId: string | null;
  insightRunId: string | null;
  onInsightCandidateChange: (candidate: InsightCandidate | null, candidateId: string | null, runId: string | null) => void;
  onProductsRefresh: () => void;
};

export function ResearchCanvas({
  workspaceId,
  detail,
  onQuestionUpdated,
  insightCandidate: _insightCandidate,
  insightCandidateId: _insightCandidateId,
  insightRunId: _insightRunId,
  onInsightCandidateChange,
  onProductsRefresh,
}: ResearchCanvasProps) {
  // 从 detail 提取字段（防御 null/undefined）
  const snapshotId =
    detail.snapshots && detail.snapshots.length > 0
      ? detail.snapshots[0].snapshot_id
      : null;
  const currentQuestionVersion = detail.current_question?.version_number ?? 1;
  const questionText = detail.current_question?.question_text ?? '(未定义研究问题)';
  const subQuestions = detail.current_question?.sub_questions ?? [];

  const [plan, setPlan] = useState<PlanDetail | null>(null);
  const [run, setRun] = useState<RunProgress | null>(null);
  const [generating, setGenerating] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<string | null>(null);
  const [dataContext, setDataContext] = useState<string | null>(null);
  const [extractingInsight, setExtractingInsight] = useState(false);
  const [resultExpanded, setResultExpanded] = useState(true);

  // 加载时恢复最近的 Run + 分析结果
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { apiListRuns, apiListPlans, apiGetPlan } = await import('../../api/research');

        // 恢复分析结果（从最新 plan 的 dag_structure 中读取）
        try {
          const plansRes = await apiListPlans(workspaceId);
          const latestPlan = plansRes?.items?.[0];
          if (latestPlan) {
            const planDetail = await apiGetPlan(workspaceId, latestPlan.plan_id);
            if (cancelled) return;
            setPlan(planDetail);
            const steps = planDetail.dag_structure?.steps || [];
            if (steps.length > 0) {
              if (steps[0].analysis_result) {
                setAnalysisResult(steps[0].analysis_result);
              }
              if (steps[0].data_context) {
                setDataContext(steps[0].data_context);
              }
              if (steps[0].insight_candidate) {
                onInsightCandidateChange(steps[0].insight_candidate, steps[0].insight_candidate_id || null, steps[0].insight_run_id || null);
              }
            }
          }
        } catch (err) {
          if (!cancelled) console.error('恢复分析状态失败', err);
        }

        // 恢复 Run
        const res = await apiListRuns(workspaceId);
        if (cancelled) return;
        const items = res?.items ?? [];
        if (items.length > 0) {
          const activeRun = items.find(
            (r) => ['queued', 'planning', 'running'].includes(r.status),
          );
          const target = activeRun ?? items[0];
          if (target) {
            const { apiGetRun } = await import('../../api/research');
            const progress = await apiGetRun(workspaceId, target.run_id);
            if (!cancelled) setRun(progress);
          }
        }
      } catch (err) {
        if (!cancelled) console.error('恢复 Run 状态失败', err);
      }
    })();
    return () => { cancelled = true; };
  }, [workspaceId]);

  // 编辑问题状态
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editQuestionText, setEditQuestionText] = useState('');
  const [editSubQuestions, setEditSubQuestions] = useState<string[]>([]);
  const [savingQuestion, setSavingQuestion] = useState(false);

  // 提炼 Insight
  const handleExtractInsight = useCallback(async () => {
    if (!plan || !snapshotId) return;
    setExtractingInsight(true);
    try {
      const { apiExtractInsight } = await import('../../api/research');
      const result = await apiExtractInsight(workspaceId, plan.plan_id, snapshotId);
      onInsightCandidateChange(result.insight_candidate, result.insight_candidate_id || null, result.run_id || null);
      onProductsRefresh();
      message.success('结论抽取完毕');
    } catch {
      message.error('结论抽取失败');
    } finally {
      setExtractingInsight(false);
    }
  }, [plan, snapshotId, workspaceId, onInsightCandidateChange, onProductsRefresh]);

  // SSE 事件处理
  // 生成计划
  const handleGeneratePlan = useCallback(async () => {
    if (!snapshotId) return;
    setGenerating(true);
    try {
      const planRef = await apiGeneratePlan(workspaceId, snapshotId);
      // 获取计划详情
      const { apiGetPlan } = await import('../../api/research');
      const detail = await apiGetPlan(workspaceId, planRef.plan_id);
      setPlan(detail);
    } catch {
      message.error('生成计划失败');
    } finally {
      setGenerating(false);
    }
  }, [workspaceId, snapshotId]);

  // 确认计划
  const handleConfirmPlan = useCallback(async () => {
    if (!plan) return;
    try {
      const { apiConfirmPlan } = await import('../../api/research');
      await apiConfirmPlan(workspaceId, plan.plan_id);
      setPlan({ ...plan, status: 'confirmed' });
      message.success('计划已确认');
    } catch {
      message.error('确认计划失败');
    }
  }, [workspaceId, plan]);

  // 打开编辑问题弹窗
  const handleOpenEdit = useCallback(() => {
    setEditQuestionText(questionText === '(未定义研究问题)' ? '' : questionText);
    setEditSubQuestions([...subQuestions]);
    setEditModalOpen(true);
  }, [questionText, subQuestions]);

  // 保存问题编辑
  const handleSaveQuestion = useCallback(async () => {
    if (!editQuestionText.trim()) {
      message.warning('请输入研究问题');
      return;
    }
    setSavingQuestion(true);
    try {
      await apiUpdateQuestion(workspaceId, {
        question_text: editQuestionText.trim(),
        sub_questions: editSubQuestions.filter((s) => s.trim()),
      });
      message.success('研究问题已更新（新版本）');
      setEditModalOpen(false);
      onQuestionUpdated?.();
    } catch {
      message.error('更新研究问题失败');
    } finally {
      setSavingQuestion(false);
    }
  }, [workspaceId, editQuestionText, editSubQuestions, onQuestionUpdated]);

  return (
    <div style={{ padding: '16px', height: '100%', overflowY: 'auto' }}>
      {/* 主研究问题 */}
      <Card
        size="small"
        style={{ marginBottom: 12 }}
        title={
          <Space>
            <Tag color="blue">v{currentQuestionVersion}</Tag>
            <Typography.Text strong>{questionText}</Typography.Text>
          </Space>
        }
        extra={
          <Space size="small">
            <Button
              size="small"
              type="text"
              icon={<EditOutlined />}
              onClick={handleOpenEdit}
            >
              编辑
            </Button>
            {snapshotId && (
              <Button
                size="small"
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={generating}
                onClick={handleGeneratePlan}
              >
                生成计划
              </Button>
            )}
          </Space>
        }
      >
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {subQuestions.length > 0 && (
            <div>
              {subQuestions.map((sq, idx) => (
                <Typography.Text key={idx} type="secondary" style={{ display: 'block', fontSize: 13 }}>
                  · {sq}
                </Typography.Text>
              ))}
            </div>
          )}
          {subQuestions.length === 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              暂无子问题，点击"编辑问题"添加
            </Typography.Text>
          )}
        </Space>
      </Card>

      {/* 分析计划区 */}
      {plan && (
        <PlanReviewCard
          plan={plan}
          workspaceId={workspaceId}
          snapshotId={snapshotId || ''}
          onConfirm={handleConfirmPlan}
          onAnalysisComplete={(result) => {
            setAnalysisResult(result.analysis_result);
      setDataContext(result.data_context || null);
          }}
        />
      )}

      {/* 分析结果区 */}
      {analysisResult && (
        <Card
          size="small"
          style={{ marginBottom: 12 }}
          title={
            <Space
              onClick={() => setResultExpanded(!resultExpanded)}
              style={{ cursor: 'pointer' }}
            >
              <BarChartOutlined style={{ color: '#1890ff' }} />
              <Typography.Text strong>分析结果</Typography.Text>
              {resultExpanded ? <UpOutlined style={{ fontSize: 11, color: '#8c8c8c' }} /> : <DownOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />}
            </Space>
          }
          extra={
            <Button
              size="small"
              type="primary"
              icon={<BulbOutlined />}
              loading={extractingInsight}
              onClick={handleExtractInsight}
            >
              {_insightCandidate ? '重新抽取结论' : '抽取结论'}
            </Button>
          }
        >
          {resultExpanded ? (
            <div className="research-markdown" style={{ fontSize: 14, lineHeight: 1.8 }}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  pre({ children }) {
                    return <>{children}</>;
                  },
                  code({ className, children, ...props }) {
                    const lang = className?.replace('language-', '') || '';
                    let codeStr = String(children || '').replace(/\n$/, '');
                    if (lang === 'chart-ref' || lang === 'chart') {
                      return <ChartRefBlock specStr={codeStr} systemContext={dataContext} />;
                    }
                    if (lang === 'echarts') {
                      // 清洗 JS 函数为字符串模板（处理嵌套大括号）
                      codeStr = codeStr.replace(
                        /"formatter"\s*:\s*function\s*\([^)]*\)\s*\{[\s\S]*?\}\s*(?=,\s*")/g,
                        '"formatter": "{b}: {c}"'
                      ).replace(
                        /"formatter"\s*:\s*\([^)]*\)\s*=>\s*\{[\s\S]*?\}\s*(?=,\s*")/g,
                        '"formatter": "{b}: {c}"'
                      );
                      // 如果还有残留的 function，直接移除整个属性
                      if (codeStr.includes('function')) {
                        codeStr = codeStr.replace(
                          /"formatter"\s*:\s*function[\s\S]*?\}\s*,/g,
                          '"formatter": "{b}: {c}",'
                        );
                      }
                      return <ChartBlock optionStr={codeStr} />;
                    }
                    if (lang === 'data') {
                      return (
                        <details style={{ margin: '8px 0' }}>
                          <summary style={{ cursor: 'pointer', fontSize: 12, color: '#8c8c8c' }}>
                            结构化数据（已提取，点击展开查看）
                          </summary>
                          <pre style={{ fontSize: 10, maxHeight: 300, overflow: 'auto', background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
                            <code>{children}</code>
                          </pre>
                        </details>
                      );
                    }
                    return <code className={className} {...props}>{children}</code>;
                  },
                }}
              >
                {analysisResult}
              </ReactMarkdown>
            </div>
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 12, cursor: 'pointer' }} onClick={() => setResultExpanded(true)}>
              {analysisResult.replace(/[#*`]/g, '').slice(0, 120)}...
            </Typography.Text>
          )}
        </Card>
      )}

      {/* 空状态 */}
      {!plan && !run && !snapshotId && (
        <Empty description="请先冻结证据快照" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}

      {/* 编辑研究问题弹窗 */}
      <Modal
        title="编辑研究问题"
        open={editModalOpen}
        onOk={handleSaveQuestion}
        onCancel={() => setEditModalOpen(false)}
        confirmLoading={savingQuestion}
        okText="保存（生成新版本）"
        cancelText="取消"
        width={560}
      >
        <div style={{ marginBottom: 12 }}>
          <Typography.Text strong style={{ display: 'block', marginBottom: 6 }}>
            主研究问题 *
          </Typography.Text>
          <Input.TextArea
            value={editQuestionText}
            onChange={(e) => setEditQuestionText(e.target.value)}
            placeholder="例：不同批次间峰值差异的来源是什么？是否有系统性因素？"
            rows={3}
          />
        </div>
        <div>
          <Space style={{ marginBottom: 6 }}>
            <Typography.Text strong>子问题</Typography.Text>
            <Button
              size="small"
              type="dashed"
              icon={<PlusOutlined />}
              onClick={() => setEditSubQuestions([...editSubQuestions, ''])}
            >
              添加
            </Button>
          </Space>
          {editSubQuestions.map((sq, idx) => (
            <Space key={idx} style={{ display: 'flex', marginBottom: 4 }}>
              <Input
                value={sq}
                onChange={(e) => {
                  const next = [...editSubQuestions];
                  next[idx] = e.target.value;
                  setEditSubQuestions(next);
                }}
                placeholder={`子问题 ${idx + 1}`}
                style={{ flex: 1 }}
              />
              <Button
                size="small"
                type="text"
                danger
                onClick={() => setEditSubQuestions(editSubQuestions.filter((_, i) => i !== idx))}
              >
                删除
              </Button>
            </Space>
          ))}
        </div>
      </Modal>
    </div>
  );
}
