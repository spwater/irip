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
import { Card, Tag, Space, Button, Empty, Spin, Typography, Modal, Input, message, Popconfirm } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChartBlock } from '../assistant/message-thread/components/ChartBlock';
import {
  PlayCircleOutlined,
  EditOutlined,
  PlusOutlined,
  UpOutlined,
  DownOutlined,
  CheckOutlined,
  CloseOutlined,
  BulbOutlined,
} from '@ant-design/icons';
import {
  apiGeneratePlan,
  apiConfirmPlan,
  apiSubmitRun,
  apiGetRun,
  apiListRunArtifacts,
  apiCancelRun,
  apiUpdateQuestion,
  type PlanDetail,
  type RunProgress,
  type Artifact,
  type CoverageDeclaration,
  type WorkspaceDetail as WorkspaceDetailType,
} from '../../api/research';
import { RunProgressPanel } from './RunProgressPanel';
import { QueueStatus } from './QueueStatus';
import { PlanReviewCard } from './PlanReviewCard';
import { useRunSSE } from './useRunSSE';
import { CandidatePreviewPanel } from './CandidatePreviewPanel';
import { ConfirmedProductsPanel } from './ConfirmedProductsPanel';
import { ProductDetailView } from './ProductDetailView';

export type ResearchCanvasProps = {
  workspaceId: string;
  detail: WorkspaceDetailType;
  onQuestionUpdated?: () => void;
};

export function ResearchCanvas({
  workspaceId,
  detail,
  onQuestionUpdated,
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
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [generating, setGenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [productsRefresh, setProductsRefresh] = useState(0);
  const [selectedProduct, setSelectedProduct] = useState<{ type: string; id: string } | null>(null);
  const [analysisResult, setAnalysisResult] = useState<string | null>(null);
  const [insightCandidate, setInsightCandidate] = useState<any | null>(null);
  const [insightCandidateId, setInsightCandidateId] = useState<string | null>(null);
  const [insightRunId, setInsightRunId] = useState<string | null>(null);
  const [accepting, setAccepting] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [extractingInsight, setExtractingInsight] = useState(false);
  const [resultExpanded, setResultExpanded] = useState(true);

  // 加载时恢复最近的 Run + 分析结果
  useEffect(() => {
    (async () => {
      try {
        const { apiListRuns, apiListPlans, apiGetPlan } = await import('../../api/research');

        // 恢复分析结果（从最新 plan 的 dag_structure 中读取）
        try {
          const plansRes = await apiListPlans(workspaceId);
          const latestPlan = plansRes?.items?.[0];
          if (latestPlan) {
            const planDetail = await apiGetPlan(workspaceId, latestPlan.plan_id);
            setPlan(planDetail);
            const steps = planDetail.dag_structure?.steps || [];
            if (steps.length > 0) {
              if (steps[0].analysis_result) {
                setAnalysisResult(steps[0].analysis_result);
              }
              if (steps[0].insight_candidate) {
                setInsightCandidate(steps[0].insight_candidate);
              }
              if (steps[0].insight_candidate_id) {
                setInsightCandidateId(steps[0].insight_candidate_id);
              }
              if (steps[0].insight_run_id) {
                setInsightRunId(steps[0].insight_run_id);
              }
            }
          }
        } catch {
          // 静默
        }

        // 恢复 Run
        const res = await apiListRuns(workspaceId);
        const items = res?.items ?? [];
        if (items.length > 0) {
          const activeRun = items.find(
            (r) => ['queued', 'planning', 'running'].includes(r.status),
          );
          const target = activeRun ?? items[0];
          if (target) {
            const { apiGetRun } = await import('../../api/research');
            const progress = await apiGetRun(workspaceId, target.run_id);
            setRun(progress);
          }
        }
      } catch {
        // 静默
      }
    })();
  }, [workspaceId]);

  // 编辑问题状态
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editQuestionText, setEditQuestionText] = useState('');
  const [editSubQuestions, setEditSubQuestions] = useState<string[]>([]);
  const [savingQuestion, setSavingQuestion] = useState(false);

  // 接受 Insight 候选
  const handleAcceptInsight = useCallback(async () => {
    if (!insightCandidateId || !workspaceId) return;
    setAccepting(true);
    try {
      const { apiAcceptCandidate } = await import('../../api/researchProducts');
      await apiAcceptCandidate(workspaceId, insightRunId || '00000000-0000-0000-0000-000000000000', insightCandidateId);
      message.success('Insight 已接受，已创建为正式产物');
      setInsightCandidate(null);
      setInsightCandidateId(null);
      setInsightRunId(null);
      setProductsRefresh((prev) => prev + 1);
    } catch {
      message.error('接受失败');
    } finally {
      setAccepting(false);
    }
  }, [insightCandidateId, workspaceId]);

  // 拒绝 Insight 候选
  const handleRejectInsight = useCallback(async () => {
    if (!insightCandidateId || !workspaceId) return;
    try {
      const { apiRejectCandidate } = await import('../../api/researchProducts');
      await apiRejectCandidate(workspaceId, insightRunId || '00000000-0000-0000-0000-000000000000', insightCandidateId, '用户拒绝');
      message.success('已拒绝');
      setInsightCandidate(null);
      setInsightCandidateId(null);
      setInsightRunId(null);
    } catch {
      message.error('操作失败');
    }
  }, [insightCandidateId, workspaceId]);

  // 从问题卡片执行数据分析
  const handleExecuteFromCard = useCallback(async () => {
    if (!plan || !snapshotId) return;
    setExecuting(true);
    try {
      const { apiAnalyzeData } = await import('../../api/research');
      const result = await apiAnalyzeData(
        workspaceId,
        plan.plan_id,
        snapshotId,
      );
      setAnalysisResult(result.analysis_result);
      message.success('数据分析完成');
    } catch {
      message.error('分析执行失败');
    } finally {
      setExecuting(false);
    }
  }, [plan, snapshotId, workspaceId]);

  // 提炼 Insight
  const handleExtractInsight = useCallback(async () => {
    if (!plan || !snapshotId) return;
    setExtractingInsight(true);
    try {
      const { apiExtractInsight } = await import('../../api/research');
      const result = await apiExtractInsight(workspaceId, plan.plan_id, snapshotId);
      setInsightCandidate(result.insight_candidate);
      setInsightCandidateId(result.insight_candidate_id || null);
      setInsightRunId(result.run_id || null);
      message.success('Insight 提取完成');
    } catch {
      message.error('Insight 提取失败');
    } finally {
      setExtractingInsight(false);
    }
  }, [plan, snapshotId, workspaceId]);

  // SSE 事件处理
  const handleSSEEvent = useCallback(
    (eventType: string, data: string) => {
      try {
        const payload = JSON.parse(data);
        if (eventType === 'run.status_changed' && run) {
          // 刷新 Run 进度
          apiGetRun(workspaceId, run.run_id).then(setRun).catch(() => {});
        } else if (eventType === 'step.status_changed' && run) {
          apiGetRun(workspaceId, run.run_id).then(setRun).catch(() => {});
        } else if (eventType === 'step.progress' && run) {
          apiGetRun(workspaceId, run.run_id).then(setRun).catch(() => {});
        } else if (eventType === 'artifact.created' && run) {
          apiListRunArtifacts(workspaceId, run.run_id).then((res) => setArtifacts(res?.items ?? [])).catch(() => {});
        }
      } catch {
        // ignore
      }
    },
    [workspaceId, run],
  );

  const { connected, fallbackToPolling } = useRunSSE({
    workspaceId,
    runId: run?.run_id ?? '',
    onEvent: handleSSEEvent,
    enabled: !!run && run.status !== 'succeeded' && run.status !== 'failed' && run.status !== 'cancelled',
  });

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
      // ignore
    } finally {
      setGenerating(false);
    }
  }, [workspaceId, snapshotId]);

  // ===== 以下多步执行流程暂时注释掉，后面再说 =====
  // // 确认计划
  // const handleConfirmPlan = useCallback(async () => {
  //   if (!plan) return;
  //   try {
  //     await apiConfirmPlan(workspaceId, plan.plan_id);
  //     setPlan({ ...plan, status: 'confirmed' });
  //   } catch {
  //     // ignore
  //   }
  // }, [workspaceId, plan]);

  // // 调整计划
  // const handleAdjustPlan = useCallback(() => {}, []);

  // // 提交 Run
  // const handleSubmitRun = useCallback(async () => {
  //   if (!plan || !snapshotId) return;
  //   setSubmitting(true);
  //   try {
  //     const runRef = await apiSubmitRun(workspaceId, plan.plan_id, snapshotId);
  //     const progress = await apiGetRun(workspaceId, runRef.run_id);
  //     setRun(progress);
  //     const artRes = await apiListRunArtifacts(workspaceId, runRef.run_id);
  //     setArtifacts(artRes?.items ?? []);
  //   } catch {
  //     // ignore
  //   } finally {
  //     setSubmitting(false);
  //   }
  // }, [workspaceId, plan, snapshotId]);

  // // 取消 Run
  // const handleCancelRun = useCallback(async () => {
  //   if (!run) return;
  //   setCancelling(true);
  //   try {
  //     await apiCancelRun(workspaceId, run.run_id);
  //     const progress = await apiGetRun(workspaceId, run.run_id);
  //     setRun(progress);
  //   } catch {
  //     // ignore
  //   } finally {
  //     setCancelling(false);
  //   }
  // }, [workspaceId, run]);

  // const isRunActive = run && ['queued', 'planning', 'running'].includes(run.status);
  // const isQueued = run?.status === 'queued' && (run.steps?.length ?? 0) === 0;

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
                生成分析计划
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
          onAnalysisComplete={(result) => {
            setAnalysisResult(result.analysis_result);
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
              {insightCandidate ? '重新提炼' : '提炼 Insight'}
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

      {/* Insight 候选区 */}
      {insightCandidate && (
        <Card
          size="small"
          title={
            <Space>
              <Typography.Text strong>Insight 候选</Typography.Text>
              <Tag color={insightCandidate.confidence_level === 'high' ? 'green' : insightCandidate.confidence_level === 'medium' ? 'orange' : 'red'}>
                {insightCandidate.confidence_level || 'unknown'}
              </Tag>
            </Space>
          }
          extra={
            insightCandidateId && (
              <Space size="small">
                <Button
                  size="small"
                  type="primary"
                  icon={<CheckOutlined />}
                  loading={accepting}
                  onClick={handleAcceptInsight}
                >
                  接受
                </Button>
                <Button
                  size="small"
                  icon={<EditOutlined />}
                  onClick={() => message.info('修改功能开发中')}
                >
                  修改
                </Button>
                <Popconfirm
                  title="确定拒绝此候选？"
                  onConfirm={handleRejectInsight}
                  okText="拒绝"
                  cancelText="取消"
                >
                  <Button size="small" danger icon={<CloseOutlined />}>
                    拒绝
                  </Button>
                </Popconfirm>
              </Space>
            )
          }
          style={{ marginBottom: 12 }}
        >
          {insightCandidate.conclusion && (
            <div style={{ marginBottom: 8 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>结论：</Typography.Text>
              <div className="research-markdown" style={{ fontSize: 14, marginTop: 2 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{insightCandidate.conclusion}</ReactMarkdown>
              </div>
            </div>
          )}
          {insightCandidate.scope && (
            <div style={{ marginBottom: 8 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>范围：</Typography.Text>
              <Typography.Text style={{ fontSize: 13 }}>{insightCandidate.scope}</Typography.Text>
            </div>
          )}
          {insightCandidate.limitations && (
            <div style={{ marginBottom: 8 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>局限：</Typography.Text>
              <Typography.Text style={{ fontSize: 13 }}>{insightCandidate.limitations}</Typography.Text>
            </div>
          )}
          {insightCandidate.evidence_source_label && (
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>证据来源：</Typography.Text>
              <Tag>{insightCandidate.evidence_source_label}</Tag>
            </div>
          )}
        </Card>
      )}

      {/* ===== 以下多步执行流程暂时注释掉，后面再说 =====
      {/ * Run 提交按钮 * /}
      {plan && plan.status === 'confirmed' && !run && (
        <Button
          type="primary"
          block
          icon={<PlayCircleOutlined />}
          loading={submitting}
          onClick={handleSubmitRun}
          style={{ marginBottom: 12 }}
        >
          执行计划
        </Button>
      )}

      {/ * 排队状态 * /}
      {isQueued && run && (
        <QueueStatus
          workspaceId={workspaceId}
          runId={run.run_id}
          onCancel={handleCancelRun}
          onCancelLoading={cancelling}
        />
      )}

      {/ * Run 进度面板 * /}
      {run && !isQueued && (
        <Card
          size="small"
          title={`Run #${run.run_number ?? ''}`}
          style={{ marginBottom: 12 }}
          extra={
            ['succeeded', 'failed', 'cancelled', 'partially_succeeded'].includes(run.status) && (
              <Button
                size="small"
                type="text"
                onClick={() => {
                  setRun(null);
                  setPlan(null);
                  setArtifacts([]);
                }}
              >
                关闭 ✕
              </Button>
            )
          }
        >
          {fallbackToPolling && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              实时连接不可用，正在轮询...
            </Typography.Text>
          )}
          <RunProgressPanel
            runId={run.run_id}
            runStatus={run.status}
            steps={run.steps}
            coverageDeclaration={run.coverage_declaration as CoverageDeclaration | null}
            startedAt={run.started_at}
            completedAt={run.completed_at}
            onCancel={handleCancelRun}
            onCancelLoading={cancelling}
          />
        </Card>
      )}
      ===== 结束注释 ===== */}

      {/* 产物详情视图（选中产物时） */}
      {selectedProduct && (
        <ProductDetailView
          workspaceId={workspaceId}
          productType={selectedProduct.type}
          productId={selectedProduct.id}
          onBack={() => setSelectedProduct(null)}
        />
      )}

      {/* ===== 候选产物预览区暂时注释掉 =====
      {!selectedProduct && run && (run.status === 'succeeded' || run.status === 'partially_succeeded') && (
        <CandidatePreviewPanel
          workspaceId={workspaceId}
          runId={run.run_id}
          onProductsChanged={() => setProductsRefresh((prev) => prev + 1)}
        />
      )}
      ===== 结束注释 ===== */}

      {/* 已确认产物列表 */}
      {!selectedProduct && (
        <ConfirmedProductsPanel
          workspaceId={workspaceId}
          refreshTrigger={productsRefresh}
          onSelectProduct={(type, id) => setSelectedProduct({ type, id })}
        />
      )}

      {/* 空状态 */}
      {!selectedProduct && !plan && !run && !snapshotId && (
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
