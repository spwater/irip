import { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate } from '@tanstack/react-router';
import {
  Button,
  Card,
  Progress,
  Result,
  Spin,
  Steps,
  Table,
  Typography,
  Upload,
  message,
} from 'antd';
import { useMutation } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateJob,
  apiGetJob,
  type JobSummary,
  type JobStatus,
} from '@/api/client';
import { apiPreviewIngestion } from '@/api/standards-objects';
import { extractApiError, type SourcePreview } from '@/api/types';
import { useJobStore } from '@/features/jobs/useJobStore';

const { Text } = Typography;

/**
 * M-08: 摄入轮询参数
 * - 指数退避：2s → 30s（每次失败翻倍，上限 30s）
 * - 连续失败阈值：5 次（达到后停止轮询并提示）
 * - 总超时：5 分钟（达到后停止并显示超时状态）
 * - 401：停止轮询并跳转登录页
 */
const MAX_POLLING_DURATION = 5 * 60 * 1000;
const MAX_CONSECUTIVE_FAILURES = 5;
const INITIAL_POLL_INTERVAL = 2000;
const MAX_POLL_INTERVAL = 30000;
const TERMINAL_JOB_STATUSES: JobStatus[] = ['succeeded', 'failed', 'cancelled'];

/** 预览行类型（带唯一 key） */
type PreviewRow = Record<string, unknown> & { _key: string };

/**
 * 数据摄入向导（标准层空表清理后精简版）
 *
 * 步骤：上传文件 → 数据预览 → 提交 → 进度 → 结果
 *
 * 原字段映射与质量校验步骤依赖已删除的映射评分端点（migration 0057），
 * 已移除。上传后直接预览并提交摄入作业。
 *
 * - 上传：Upload.Dragger 选择文件后自动调用 preview API
 * - 预览：展示来源数据表格
 * - 提交：创建摄入作业
 * - 进度：轮询作业状态
 * - 结果：展示成功/失败摘要
 */
export function IngestionWizard(): JSX.Element {
  const [currentStep, setCurrentStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [previewData, setPreviewData] = useState<SourcePreview | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobSummary | null>(null);

  // ---- 预览 Mutation ----
  const previewMutation = useMutation({
    mutationFn: (f: File) => apiPreviewIngestion(f),
    onSuccess: (data) => {
      setPreviewData(data);
      setCurrentStep(1);
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 提交作业 Mutation ----
  const submitMutation = useMutation({
    mutationFn: () =>
      apiCreateJob(
        'ingestions.import',
        {
          file_name: file?.name ?? '',
        },
        Date.now().toString(),
      ),
    onSuccess: (data) => {
      setJobId(data.job_id);
      useJobStore.getState().addJob(data.job_id);
      setCurrentStep(3);
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- M-08: 轮询作业状态（指数退避 + 连续失败阈值 + 总超时）----
  const navigate = useNavigate();
  const startTimeRef = useRef<number>(0);
  const consecutiveFailuresRef = useRef<number>(0);
  const currentIntervalRef = useRef<number>(INITIAL_POLL_INTERVAL);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRef = useRef<boolean>(true);

  const poll = useCallback(async (): Promise<void> => {
    if (!activeRef.current || !jobId) return;

    // 总超时检查
    if (Date.now() - startTimeRef.current > MAX_POLLING_DURATION) {
      if (activeRef.current) {
        setJobStatus({
          id: jobId,
          kind: 'ingestions.import',
          status: 'failed',
          stage: '摄入状态轮询超时，请稍后查看结果',
          progress: 0,
          retryable: true,
        });
        setCurrentStep(4);
        message.warning('摄入状态轮询超时，请稍后查看结果');
      }
      return;
    }

    try {
      const status = await apiGetJob(jobId);
      if (!activeRef.current) return;

      // 成功：重置失败计数和轮询间隔
      consecutiveFailuresRef.current = 0;
      currentIntervalRef.current = INITIAL_POLL_INTERVAL;
      setJobStatus(status);

      // 终态：停止轮询
      if (TERMINAL_JOB_STATUSES.includes(status.status)) {
        setCurrentStep(4);
        return;
      }
    } catch (error) {
      if (!activeRef.current) return;

      consecutiveFailuresRef.current++;
      // 指数退避：每次失败翻倍，上限 30s
      currentIntervalRef.current = Math.min(
        currentIntervalRef.current * 2,
        MAX_POLL_INTERVAL,
      );

      // 401：停止轮询并跳转登录（clearSessionState 已由拦截器处理）
      const errStatus = (error as { response?: { status?: number } }).response?.status;
      if (errStatus === 401) {
        message.warning('登录已过期，请重新登录');
        void navigate({ to: '/login' });
        return;
      }

      // 连续失败达到阈值：停止轮询并提示
      if (consecutiveFailuresRef.current >= MAX_CONSECUTIVE_FAILURES) {
        setJobStatus({
          id: jobId,
          kind: 'ingestions.import',
          status: 'failed',
          stage: '连续多次获取状态失败，请检查网络后重试',
          progress: 0,
          retryable: true,
        });
        setCurrentStep(4);
        message.error('连续多次获取状态失败，请检查网络后重试');
        return;
      }
    }

    // 安排下一轮轮询（使用当前退避间隔）
    if (activeRef.current) {
      timeoutRef.current = setTimeout(() => void poll(), currentIntervalRef.current);
    }
  }, [jobId, navigate]);

  useEffect(() => {
    if (currentStep !== 3 || !jobId) return;

    // 初始化轮询状态
    activeRef.current = true;
    startTimeRef.current = Date.now();
    consecutiveFailuresRef.current = 0;
    currentIntervalRef.current = INITIAL_POLL_INTERVAL;

    void poll();

    return () => {
      activeRef.current = false;
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };
  }, [currentStep, jobId, poll]);

  // ---- 事件处理 ----
  const handleSubmit = (): void => {
    setCurrentStep(2);
    submitMutation.mutate();
  };

  const handleReset = (): void => {
    // M-08: 清理轮询状态
    activeRef.current = false;
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    consecutiveFailuresRef.current = 0;
    currentIntervalRef.current = INITIAL_POLL_INTERVAL;

    setCurrentStep(0);
    setFile(null);
    setPreviewData(null);
    setJobId(null);
    setJobStatus(null);
  };

  // ---- 预览表格列 ----
  const previewColumns: ColumnsType<PreviewRow> = (previewData?.columns ?? []).map(
    (col) => ({
      title: col.name,
      dataIndex: col.name,
      key: col.name,
    }),
  );

  const previewRows: PreviewRow[] = (previewData?.rows ?? []).map((row, idx) => ({
    ...row,
    _key: String(idx),
  }));

  return (
    <Card>
      <Steps
        current={currentStep}
        items={[
          { title: '上传文件' },
          { title: '数据预览' },
          { title: '提交' },
          { title: '进度' },
          { title: '结果' },
        ]}
        style={{ marginBottom: 24 }}
      />

      {/* Step 0: 上传文件 */}
      {currentStep === 0 && (
        <Upload.Dragger
          beforeUpload={(f) => {
            setFile(f);
            previewMutation.mutate(f);
            return false;
          }}
          accept=".xlsx,.xls,.csv"
          showUploadList={false}
          disabled={previewMutation.isPending}
        >
          {previewMutation.isPending ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Spin size="large" />
              <p style={{ marginTop: 16, color: 'var(--ocean-text-muted)' }}>正在上传...</p>
            </div>
          ) : (
            <>
              <p style={{ fontSize: 16, margin: '8px 0' }}>
                点击或拖拽文件到此区域上传
              </p>
              <p style={{ color: 'var(--ocean-text-muted)', margin: 0 }}>支持 Excel、CSV 格式</p>
            </>
          )}
        </Upload.Dragger>
      )}

      {/* Step 1: 数据预览 */}
      {currentStep === 1 && previewData && (
        <div>
          <div style={{ marginBottom: 16 }}>
            <Text strong>
              共 {previewData.total_rows} 行，{previewData.columns.length} 列
            </Text>
          </div>
          <Table<PreviewRow>
            columns={previewColumns}
            dataSource={previewRows}
            rowKey="_key"
            pagination={false}
            size="small"
            scroll={{ x: true }}
          />
          <div style={{ marginTop: 16 }}>
            <Button
              type="primary"
              loading={submitMutation.isPending}
              onClick={handleSubmit}
            >
              提交
            </Button>
          </div>
        </div>
      )}

      {/* Step 2: 提交 */}
      {currentStep === 2 && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin size="large" />
          <p style={{ marginTop: 16, color: 'var(--ocean-text-muted)' }}>正在创建作业...</p>
        </div>
      )}

      {/* Step 3: 进度 */}
      {currentStep === 3 && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Progress percent={jobStatus?.progress ?? 0} />
          <p style={{ marginTop: 16 }}>
            <Text>{jobStatus?.stage ?? '等待中...'}</Text>
          </p>
        </div>
      )}

      {/* Step 4: 结果 */}
      {currentStep === 4 && (
        <Result
          status={jobStatus?.status === 'succeeded' ? 'success' : 'error'}
          title={jobStatus?.status === 'succeeded' ? '导入成功' : '导入失败'}
          subTitle={jobStatus?.stage}
          extra={
            <Button type="primary" onClick={handleReset}>
              重新开始
            </Button>
          }
        />
      )}
    </Card>
  );
}
