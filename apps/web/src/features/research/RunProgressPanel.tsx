/**
 * RunProgressPanel — Run 进度面板
 *
 * 功能：
 * - 总体进度条（已完成步骤数 / 总步骤数）
 * - 步骤状态列表（DAG 线性展示）
 *   - 状态图标：✓ succeeded / ● running + 进度% / ✗ failed / ○ pending / ⊘ skipped
 *   - 每步显示：步骤名称、问题摘要、执行方式、分析模式
 * - 运行时长计时器
 * - 覆盖声明条："自动模式: 混合分析 | 数据覆盖率 100% | LLM 阅读率 75% | 是否抽样: 否"
 * - 取消按钮 → apiCancelRun
 *
 * Run 状态色板：
 * queued 灰色 / planning 蓝色 / running 蓝色脉冲 / partially_succeeded 橙色 / succeeded 绿色 / failed 红色 / cancelled 灰色
 */

import { useMemo } from 'react';
import { Progress, Tag, Button, Space, Tooltip } from 'antd';
import {
  CheckCircleOutlined,
  LoadingOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  StopOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import type { RunProgress, CoverageDeclaration } from '../../api/research';

export type RunProgressPanelProps = {
  runId: string;
  runStatus: string;
  steps: RunProgress['steps'];
  coverageDeclaration: CoverageDeclaration | null;
  startedAt: string | null;
  completedAt: string | null;
  onCancel: () => void;
  onCancelLoading?: boolean;
};

// Run 状态色板
const STATUS_COLORS: Record<string, string> = {
  queued: '#d9d9d9',
  planning: '#1890ff',
  running: '#1890ff',
  partially_succeeded: '#faad14',
  succeeded: '#52c41a',
  failed: '#ff4d4f',
  cancelled: '#8c8c8c',
};

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  planning: '规划中',
  running: '运行中',
  partially_succeeded: '部分成功',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
};

// 步骤状态图标
const STEP_STATUS_ICONS: Record<string, React.ReactNode> = {
  succeeded: <CheckCircleOutlined style={{ color: '#52c41a' }} />,
  running: <LoadingOutlined style={{ color: '#1890ff' }} />,
  failed: <CloseCircleOutlined style={{ color: '#ff4d4f' }} />,
  pending: <ClockCircleOutlined style={{ color: '#d9d9d9' }} />,
  skipped: <StopOutlined style={{ color: '#8c8c8c' }} />,
  cancelled: <StopOutlined style={{ color: '#8c8c8c' }} />,
};

const METHOD_LABELS: Record<string, string> = {
  python: 'Python',
  llm: 'LLM',
  knowledge: '知识库',
  mixed: '混合',
};

const MODE_LABELS: Record<string, string> = {
  full_compute: '全量计算',
  chunked_full_scan: '分块全量扫描',
  direct_full_context: '直接全量上下文',
  retrieval: '检索探索',
  mixed: '混合分析',
};

export function RunProgressPanel({
  runId: _runId,
  runStatus,
  steps,
  coverageDeclaration,
  startedAt,
  completedAt,
  onCancel,
  onCancelLoading,
}: RunProgressPanelProps) {
  const totalSteps = steps.length;
  const completedSteps = steps.filter(
    (s) => s.status === 'succeeded' || s.status === 'failed' || s.status === 'skipped',
  ).length;
  const progressPercent = totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0;

  // 计算运行时长
  const duration = useMemo(() => {
    if (!startedAt) return '';
    const start = new Date(startedAt).getTime();
    const end = completedAt ? new Date(completedAt).getTime() : Date.now();
    const seconds = Math.floor((end - start) / 1000);
    if (seconds < 60) return `${seconds}秒`;
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${minutes}分${secs}秒`;
  }, [startedAt, completedAt]);

  // 覆盖声明显示
  const coverageText = useMemo(() => {
    if (!coverageDeclaration) return null;
    const modeLabel = MODE_LABELS[coverageDeclaration.analysis_mode] || coverageDeclaration.analysis_mode;
    const sampling = coverageDeclaration.is_sampled ? '是' : '否';
    return `自动模式: ${modeLabel} | 数据覆盖率 ${Math.round(coverageDeclaration.data_coverage_rate * 100)}% | LLM 阅读率 ${Math.round(coverageDeclaration.llm_read_rate * 100)}% | 是否抽样: ${sampling}`;
  }, [coverageDeclaration]);

  const isActive = runStatus === 'running' || runStatus === 'planning';

  return (
    <div style={{ padding: '16px 0' }}>
      {/* Run 状态 + 进度条 */}
      <div style={{ marginBottom: 16 }}>
        <Space align="center" style={{ marginBottom: 8 }}>
          <Tag color={STATUS_COLORS[runStatus] || '#d9d9d9'}>
            {STATUS_LABELS[runStatus] || runStatus}
          </Tag>
          {duration && <span style={{ color: '#8c8c8c', fontSize: 13 }}>{duration}</span>}
          {isActive && (
            <Button
              size="small"
              danger
              onClick={onCancel}
              loading={onCancelLoading}
            >
              取消运行
            </Button>
          )}
        </Space>
        <Progress
          percent={progressPercent}
          status={
            runStatus === 'failed' ? 'exception' :
            runStatus === 'succeeded' ? 'success' :
            runStatus === 'cancelled' ? 'normal' :
            'active'
          }
          format={() => `${completedSteps} / ${totalSteps} 步`}
        />
      </div>

      {/* 覆盖声明条 */}
      {coverageText && (
        <div
          style={{
            padding: '8px 12px',
            background: '#f5f5f5',
            borderRadius: 6,
            marginBottom: 16,
            fontSize: 13,
            color: '#595959',
          }}
        >
          {coverageText}
          {coverageDeclaration?.batch_count && coverageDeclaration.batch_progress && (
            <span style={{ marginLeft: 12 }}>
              批次 {coverageDeclaration.batch_progress}/{coverageDeclaration.batch_count} 进行中
            </span>
          )}
        </div>
      )}

      {/* 步骤列表 */}
      <div>
        {steps.map((step) => (
          <div
            key={step.step_id}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              padding: '8px 0',
              borderBottom: '1px solid #f0f0f0',
            }}
          >
            <div style={{ marginRight: 8, marginTop: 2 }}>
              {STEP_STATUS_ICONS[step.status] || <ClockCircleOutlined style={{ color: '#d9d9d9' }} />}
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 500, fontSize: 14 }}>
                {step.step_key}
                <span style={{ marginLeft: 8, color: '#8c8c8c', fontSize: 12 }}>
                  {METHOD_LABELS[step.method] || step.method}
                  {step.analysis_mode && ` · ${MODE_LABELS[step.analysis_mode] || step.analysis_mode}`}
                </span>
              </div>
              {step.error_message && (
                <Tooltip title={step.error_message}>
                  <div style={{ color: '#ff4d4f', fontSize: 12, marginTop: 2 }}>
                    <ExclamationCircleOutlined /> {step.error_message.slice(0, 80)}
                    {step.error_message.length > 80 ? '...' : ''}
                  </div>
                </Tooltip>
              )}
              {step.coverage_rate !== null && step.coverage_rate !== undefined && (
                <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 2 }}>
                  数据覆盖率 {Math.round(step.coverage_rate * 100)}%
                  {step.llm_read_rate !== null && ` | LLM 阅读率 ${Math.round(step.llm_read_rate * 100)}%`}
                  {step.is_sampled && ' | 抽样'}
                  {step.attempt_count > 1 && ` | 尝试 ${step.attempt_count} 次`}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
