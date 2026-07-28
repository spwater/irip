import { useEffect, useState } from 'react';
import {
  Button,
  Card,
  Checkbox,
  Progress,
  Result,
  Spin,
  Steps,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import { useMutation } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateJob,
  apiGetJob,
  apiPreviewIngestion,
  apiRankMappings,
  extractApiError,
  type JobSummary,
  type MappingCandidate,
  type SourcePreview,
} from '@/api/client';

const { Text } = Typography;

/** 质量等级 → 颜色 */
const QUALITY_COLOR: Record<string, string> = {
  Q0: 'default',
  Q1: 'blue',
  Q2: 'gold',
  Q3: 'green',
};

/** 根据匹配分数推断质量等级 */
function scoreToQuality(score: number): {
  level: string;
  result: 'pass' | 'warn' | 'fail';
} {
  if (score >= 0.9) return { level: 'Q3', result: 'pass' };
  if (score >= 0.7) return { level: 'Q2', result: 'pass' };
  if (score >= 0.5) return { level: 'Q1', result: 'warn' };
  return { level: 'Q0', result: 'fail' };
}

/** 预览行类型（带唯一 key） */
type PreviewRow = Record<string, unknown> & { _key: string };

/** 质量校验结果类型 */
type ValidationResult = MappingCandidate & {
  level: string;
  result: 'pass' | 'warn' | 'fail';
};

/**
 * 数据摄入向导
 *
 * 步骤：上传文件 → 数据预览 → 字段映射 → 质量校验 → 提交 → 进度 → 结果
 *
 * - 上传：Upload.Dragger 选择文件后自动调用 preview API
 * - 预览：展示来源数据表格
 * - 映射：展示建议映射（含匹配度与原因），每个映射需逐一确认后「确认并导入」按钮才可用
 * - 校验：展示质量检查结果（Q0-Q3，通过/警告/不通过）
 * - 提交：创建摄入作业
 * - 进度：轮询作业状态
 * - 结果：展示成功/失败摘要
 */
export function IngestionWizard(): JSX.Element {
  const [currentStep, setCurrentStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [previewData, setPreviewData] = useState<SourcePreview | null>(null);
  const [mappings, setMappings] = useState<MappingCandidate[]>([]);
  const [confirmedMappings, setConfirmedMappings] = useState<Record<string, boolean>>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobSummary | null>(null);

  /** 所有映射是否已确认 */
  const allMappingsConfirmed =
    mappings.length > 0 &&
    mappings.every((m) => confirmedMappings[m.variableVersionId] === true);

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

  // ---- 映射排名 Mutation ----
  const mappingMutation = useMutation({
    mutationFn: (columns: string[]) => apiRankMappings({ columns }),
    onSuccess: (data) => {
      setMappings(data.candidates);
      setConfirmedMappings({});
      setCurrentStep(2);
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
          mappings: confirmedMappings,
        },
        Date.now().toString(),
      ),
    onSuccess: (data) => {
      setJobId(data.job_id);
      setCurrentStep(5);
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
      setCurrentStep(3);
    },
  });

  // ---- 轮询作业状态 ----
  useEffect(() => {
    if (currentStep !== 5 || !jobId) return;
    let active = true;
    const poll = async (): Promise<void> => {
      if (!active) return;
      try {
        const status = await apiGetJob(jobId);
        if (!active) return;
        setJobStatus(status);
        if (['succeeded', 'failed', 'cancelled'].includes(status.status)) {
          active = false;
          setCurrentStep(6);
        }
      } catch {
        // 忽略轮询错误
      }
    };
    void poll();
    const interval = setInterval(() => void poll(), 2000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [currentStep, jobId]);

  // ---- 事件处理 ----
  const handleNextToMapping = (): void => {
    if (previewData) {
      mappingMutation.mutate(previewData.columns.map((c) => c.name));
    }
  };

  const handleConfirmImport = (): void => {
    setCurrentStep(3);
  };

  const handleSubmit = (): void => {
    setCurrentStep(4);
    submitMutation.mutate();
  };

  const handleReset = (): void => {
    setCurrentStep(0);
    setFile(null);
    setPreviewData(null);
    setMappings([]);
    setConfirmedMappings({});
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

  // ---- 质量校验结果 ----
  const validationResults: ValidationResult[] = mappings.map((m) => {
    const q = scoreToQuality(m.score);
    return { ...m, ...q };
  });

  const validationColumns: ColumnsType<ValidationResult> = [
    {
      title: '变量',
      dataIndex: 'variableCode',
      key: 'variableCode',
    },
    {
      title: '匹配度',
      dataIndex: 'score',
      key: 'score',
      width: 100,
      render: (s: number) => `${(s * 100).toFixed(0)}%`,
    },
    {
      title: '质量等级',
      dataIndex: 'level',
      key: 'level',
      width: 100,
      render: (l: string) => (
        <Tag color={QUALITY_COLOR[l] ?? 'default'}>{l}</Tag>
      ),
    },
    {
      title: '结果',
      dataIndex: 'result',
      key: 'result',
      width: 100,
      render: (r: string) => {
        const color = r === 'pass' ? 'green' : r === 'warn' ? 'orange' : 'red';
        const label = r === 'pass' ? '通过' : r === 'warn' ? '警告' : '不通过';
        return <Tag color={color}>{label}</Tag>;
      },
    },
  ];

  return (
    <Card>
      <Steps
        current={currentStep}
        items={[
          { title: '上传文件' },
          { title: '数据预览' },
          { title: '字段映射' },
          { title: '质量校验' },
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
              loading={mappingMutation.isPending}
              onClick={handleNextToMapping}
            >
              下一步
            </Button>
          </div>
        </div>
      )}

      {/* Step 2: 字段映射 */}
      {currentStep === 2 && (
        <div>
          <div style={{ marginBottom: 16 }}>
            <Text strong>请确认以下字段映射：</Text>
          </div>
          {mappings.map((m) => (
            <Card
              key={m.variableVersionId}
              size="small"
              style={{ marginBottom: 8 }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 12,
                }}
              >
                <Checkbox
                  checked={confirmedMappings[m.variableVersionId] === true}
                  onChange={(e) =>
                    setConfirmedMappings((prev) => ({
                      ...prev,
                      [m.variableVersionId]: e.target.checked,
                    }))
                  }
                >
                  确认
                </Checkbox>
                <div style={{ flex: 1 }}>
                  <div>
                    <Text strong>变量: {m.variableCode}</Text>
                  </div>
                  <div>
                    <Text type="secondary">
                      匹配度: {(m.score * 100).toFixed(0)}%
                    </Text>
                  </div>
                  <div>
                    <Text type="secondary">原因: {m.reasons.join('、')}</Text>
                  </div>
                </div>
              </div>
            </Card>
          ))}
          <div style={{ marginTop: 16 }}>
            <Button
              type="primary"
              disabled={!allMappingsConfirmed}
              onClick={handleConfirmImport}
            >
              确认并导入
            </Button>
          </div>
        </div>
      )}

      {/* Step 3: 质量校验 */}
      {currentStep === 3 && (
        <div>
          <div style={{ marginBottom: 16 }}>
            <Text strong>质量校验结果：</Text>
          </div>
          <Table<ValidationResult>
            columns={validationColumns}
            dataSource={validationResults.map((v) => ({
              ...v,
              key: v.variableVersionId,
            }))}
            pagination={false}
            size="small"
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

      {/* Step 4: 提交 */}
      {currentStep === 4 && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin size="large" />
          <p style={{ marginTop: 16, color: 'var(--ocean-text-muted)' }}>正在创建作业...</p>
        </div>
      )}

      {/* Step 5: 进度 */}
      {currentStep === 5 && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Progress percent={jobStatus?.progress ?? 0} />
          <p style={{ marginTop: 16 }}>
            <Text>{jobStatus?.stage ?? '等待中...'}</Text>
          </p>
        </div>
      )}

      {/* Step 6: 结果 */}
      {currentStep === 6 && (
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
