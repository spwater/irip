import { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Form,
  InputNumber,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate, useSearch } from '@tanstack/react-router';
import type { ColumnsType } from 'antd/es/table';
import {
  apiGetModel,
  apiGetModelVersions,
  apiListModels,
  apiPredictModel,
  extractApiError,
  type ModelSummary,
  type ModelVersionSummary,
  type PredictionResult,
} from '@/api/client';
import { PageIntro, DetailSection } from '@/components/ui';

const { Text, Paragraph } = Typography;

/** 适用域条目 */
type ApplicabilityEntry = {
  field: string;
  min: number | null;
  max: number | null;
  unit: string | null;
};

/** 预测结果行 */
type PredictionRow = {
  key: string;
  output: string;
  value: string;
  unit: string | null;
  confidence: number | null;
};

/**
 * 预测工作台（IRIP V2-T05）
 *
 * 功能：
 * - 模型选择 Select（仅展示已发布模型）
 * - 输入参数表单（根据当前版本 applicability_domain 动态生成）
 * - 「运行模型」按钮
 * - 结果展示区域（输出值 + 单位 + 置信度）
 * - 「查看运行事实」链接（当 fact_id 存在时）
 * - 警告展示（适用域外提示）
 *
 * Data Ocean Phase 4：建立稳定的命名 region（模型选择 / 预测输入 / 预测结果），
 * 保留动态输入、select 行为、适用域警告、prediction mutation、结果 Descriptions、fact 链接和单位不变。
 */
export function PredictionWorkbench(): JSX.Element {
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const initialModelId = String((search as Record<string, unknown>).modelId ?? '');

  const [selectedModelId, setSelectedModelId] = useState<string | undefined>(
    initialModelId || undefined,
  );
  const [form] = Form.useForm();
  const [warnings, setWarnings] = useState<string[]>([]);
  const [result, setResult] = useState<PredictionResult | null>(null);

  // ---- 模型列表查询 ----
  const { data: modelsData, isLoading: modelsLoading } = useQuery({
    queryKey: ['models'],
    queryFn: () => apiListModels(),
  });

  const models: ModelSummary[] = (modelsData?.items ?? []).filter(
    (m) => m.status === 'published' && m.current_version_id,
  );

  // ---- 选中模型详情 ----
  const { data: model } = useQuery({
    queryKey: ['model', selectedModelId],
    queryFn: () => apiGetModel(selectedModelId!),
    enabled: !!selectedModelId,
  });

  // ---- 选中模型版本列表 ----
  const { data: versions } = useQuery({
    queryKey: ['model-versions', selectedModelId],
    queryFn: () => apiGetModelVersions(selectedModelId!),
    enabled: !!selectedModelId,
  });

  // ---- 当前版本（用于适用域） ----
  const currentVersion: ModelVersionSummary | undefined = useMemo(() => {
    if (!model || !versions) return undefined;
    return versions.find((v) => v.id === model.current_version_id);
  }, [model, versions]);

  // ---- 适用域条目 ----
  const applicabilityEntries: ApplicabilityEntry[] = useMemo(() => {
    if (!currentVersion) return [];
    return parseApplicabilityDomain(currentVersion.applicability_domain);
  }, [currentVersion]);

  // ---- 预测 Mutation ----
  const predictMutation = useMutation({
    mutationFn: (vars: { modelId: string; inputs: Record<string, unknown> }) =>
      apiPredictModel(vars.modelId, { inputs: vars.inputs }),
    onSuccess: (data) => {
      setResult(data);
      message.success('预测完成');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
      setResult(null);
    },
  });

  // ---- 事件处理 ----
  const handleModelChange = (val: string | undefined): void => {
    setSelectedModelId(val);
    setResult(null);
    setWarnings([]);
    form.resetFields();
  };

  const handlePredict = async (): Promise<void> => {
    if (!selectedModelId) {
      message.warning('请先选择模型');
      return;
    }
    try {
      const values = await form.validateFields();
      const inputs: Record<string, unknown> = {};
      const newWarnings: string[] = [];
      for (const entry of applicabilityEntries) {
        const raw = values[entry.field];
        if (raw == null || raw === '') {
          message.warning(`请填写 ${entry.field}`);
          return;
        }
        const num = Number(raw);
        inputs[entry.field] = num;
        // 适用域校验
        if (entry.min != null && num < entry.min) {
          newWarnings.push(
            `${entry.field} = ${num} 低于适用域下限 ${entry.min}${entry.unit ? ` ${entry.unit}` : ''}`,
          );
        }
        if (entry.max != null && num > entry.max) {
          newWarnings.push(
            `${entry.field} = ${num} 超过适用域上限 ${entry.max}${entry.unit ? ` ${entry.unit}` : ''}`,
          );
        }
      }
      setWarnings(newWarnings);
      predictMutation.mutate({ modelId: selectedModelId, inputs });
    } catch {
      // 校验失败
    }
  };

  // ---- 预测结果行 ----
  const resultRows: PredictionRow[] = useMemo(() => {
    if (!result) return [];
    const confidence = readNumber(result.metadata, 'confidence');
    const units = result.metadata['units'];
    return Object.entries(result.predictions).map(([key, value]) => ({
      key,
      output: key,
      value: formatValue(value),
      unit: extractUnit(units, key),
      confidence,
    }));
  }, [result]);

  const resultColumns: ColumnsType<PredictionRow> = [
    { title: '输出', dataIndex: 'output', key: 'output', width: 200 },
    { title: '预测值', dataIndex: 'value', key: 'value', width: 160 },
    { title: '单位', dataIndex: 'unit', key: 'unit', width: 100, render: (u: string | null) => u ?? '-' },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 100,
      render: (c: number | null) => (c != null ? `${(c * 100).toFixed(1)}%` : '-'),
    },
  ];

  return (
    <div>
      <PageIntro
        index="PREDICT"
        title="预测工作台"
        description="选择已发布模型，输入参数，运行预测并查看结果"
        actions={
          <Button onClick={() => void navigate({ to: '/models' })}>
            返回模型列表
          </Button>
        }
      >
        {/* 模型选择（命名 region） */}
        <section aria-label="模型选择" style={{ marginBottom: 16 }}>
          <Select
            placeholder="选择已发布的模型"
            style={{ width: 400 }}
            loading={modelsLoading}
            value={selectedModelId}
            onChange={handleModelChange}
            options={models.map((m) => ({
              value: m.id,
              label: `${m.display_name} (${m.code})`,
            }))}
          />
          {model && (
            <div style={{ marginTop: 12 }}>
              <Space size="small">
                <Tag color="green">{model.status}</Tag>
                <Text type="secondary">{model.display_name}</Text>
              </Space>
            </div>
          )}
        </section>

        {!selectedModelId ? (
          <Empty description="请选择一个已发布的模型" />
        ) : !currentVersion ? (
          <div style={{ textAlign: 'center', padding: 24 }}>
            <Spin tip="加载模型版本…" />
          </div>
        ) : (
          <>
            {/* 输入参数表单（命名 region） */}
            <section aria-label="预测输入" style={{ marginBottom: 16 }}>
              <DetailSection title="输入参数">
                {applicabilityEntries.length === 0 ? (
                  <Alert
                    type="warning"
                    showIcon
                    message="该模型版本未定义适用域，将使用通用 JSON 输入。"
                  />
                ) : (
                  <Form form={form} layout="vertical">
                    {applicabilityEntries.map((entry) => (
                      <Form.Item
                        key={entry.field}
                        name={entry.field}
                        label={
                          <Space size="small">
                            <Text strong>{entry.field}</Text>
                            {entry.unit && <Text type="secondary">({entry.unit})</Text>}
                          </Space>
                        }
                        rules={[{ required: true, message: `请输入 ${entry.field}` }]}
                      >
                        <InputNumber
                          style={{ width: 280 }}
                          placeholder={
                            entry.min != null && entry.max != null
                              ? `范围 ${entry.min} ~ ${entry.max}`
                              : '请输入数值'
                          }
                          min={entry.min ?? undefined}
                          max={entry.max ?? undefined}
                          step={0.01}
                        />
                      </Form.Item>
                    ))}
                  </Form>
                )}
                <Button
                  type="primary"
                  onClick={handlePredict}
                  loading={predictMutation.isPending}
                  disabled={applicabilityEntries.length === 0}
                >
                  运行模型
                </Button>
              </DetailSection>
            </section>

            {/* 适用域警告 */}
            {warnings.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="输入超出适用域范围"
                description={
                  <ul style={{ margin: 0, paddingLeft: 20 }}>
                    {warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                }
              />
            )}

            {/* 结果展示（命名 region） */}
            <section aria-label="预测结果">
              {result && (
                <DetailSection title="预测结果">
                  {/* 结构化结果始终可见 */}
                  <Descriptions bordered column={2} size="small" style={{ marginBottom: 16 }}>
                    <Descriptions.Item label="模型版本 ID">
                      <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>
                        {result.model_version_id.slice(0, 12)}…
                      </Text>
                    </Descriptions.Item>
                    <Descriptions.Item label="版本号">v{result.version}</Descriptions.Item>
                  </Descriptions>

                  <Table<PredictionRow>
                    columns={resultColumns}
                    dataSource={resultRows}
                    rowKey="key"
                    pagination={false}
                    size="small"
                  />

                  {result.fact_id && (
                    <div style={{ marginTop: 16 }}>
                      <Button
                        type="link"
                        onClick={() =>
                          void navigate({ to: '/facts/$factId', params: { factId: result.fact_id! } })
                        }
                      >
                        查看运行事实 →
                      </Button>
                    </div>
                  )}

                  {Object.keys(result.metadata).length > 0 && (
                    <div style={{ marginTop: 16 }}>
                      <DetailSection title="元数据" technical>
                        <pre
                          className="ocean-md-pre"
                          style={{ maxHeight: 200 }}
                        >
                          {JSON.stringify(result.metadata, null, 2)}
                        </pre>
                      </DetailSection>
                    </div>
                  )}
                </DetailSection>
              )}

              {!result && !predictMutation.isPending && (
                <Paragraph type="secondary">点击「运行模型」开始预测</Paragraph>
              )}
            </section>
          </>
        )}
      </PageIntro>
    </div>
  );
}

/**
 * 解析适用域字典为条目列表。
 *
 * 适用域格式：`{ field_name: { min, max, unit } }`。
 */
function parseApplicabilityDomain(
  domain: Record<string, unknown>,
): ApplicabilityEntry[] {
  const entries: ApplicabilityEntry[] = [];
  for (const [field, spec] of Object.entries(domain)) {
    if (spec && typeof spec === 'object') {
      const s = spec as { min?: number; max?: number; unit?: string };
      entries.push({
        field,
        min: typeof s.min === 'number' ? s.min : null,
        max: typeof s.max === 'number' ? s.max : null,
        unit: typeof s.unit === 'string' ? s.unit : null,
      });
    }
  }
  return entries;
}

/** 从 metadata 中安全读取数值。 */
function readNumber(
  metadata: Record<string, unknown>,
  key: string,
): number | null {
  const val = metadata[key];
  return typeof val === 'number' ? val : null;
}

/** 格式化预测值为字符串。 */
function formatValue(value: unknown): string {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value.toFixed(4) : String(value);
  }
  return String(value);
}

/** 从 metadata.units 中提取指定输出键的单位。 */
function extractUnit(
  units: unknown,
  key: string,
): string | null {
  if (units && typeof units === 'object') {
    const u = (units as Record<string, unknown>)[key];
    return typeof u === 'string' ? u : null;
  }
  return null;
}

export default PredictionWorkbench;
