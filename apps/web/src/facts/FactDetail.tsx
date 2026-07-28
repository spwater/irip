import { useMemo, useState } from 'react';
import {
  Button,
  Descriptions,
  Radio,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import {
  apiGetFact,
  apiGetFactData,
  apiGetArtifactDownloadUrl,
  apiListFactRevisions,
  apiGetFactObservations,
} from '@/api/client';
import { PageIntro, DataHero, DetailSection, StatusMark, FeedbackState } from '@/components/ui';

const { Text } = Typography;

/** 把 UTC 时间字符串转成本地时间显示 */
function fmtTime(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString('zh-CN', { hour12: false });
}

/** 事实状态 → StatusTone 映射 */
function factStatusTone(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'active' || status === 'published') return 'success';
  if (status === 'draft' || status === 'pending') return 'warning';
  if (status === 'error' || status === 'failed') return 'danger';
  return 'neutral';
}

/**
 * 实验数据详情页面
 *
 * 命名 region：事实元数据 / 版本历史 / 观测数据 / 原始数据
 * 使用 PageIntro/DataHero/DetailSection/StatusMark/FeedbackState 组件
 */
export function FactDetail(): JSX.Element {
  const params = useParams({ strict: false });
  const factId = String((params as Record<string, unknown>).factId ?? '');
  const navigate = useNavigate();
  const [viewMode, setViewMode] = useState<'table' | 'json'>('table');

  // ---- 事实详情查询 ----
  const { data: fact, isLoading: factLoading, isError: factError, refetch: refetchFact, error: factErr } = useQuery({
    queryKey: ['fact', factId],
    queryFn: () => apiGetFact(factId),
    enabled: !!factId,
  });

  // ---- 事实数据查询 ----
  const { data: factData, isError: dataError, refetch: refetchData } = useQuery({
    queryKey: ['fact-data', factId],
    queryFn: () => apiGetFactData(factId),
    enabled: !!factId,
  });

  // ---- 版本历史查询 ----
  const { data: revisionsData, isLoading: revisionsLoading } = useQuery({
    queryKey: ['fact-revisions', factId],
    queryFn: () => apiListFactRevisions(factId),
    enabled: !!factId,
  });

  // ---- 观测数据查询 ----
  const { data: observationsData, isLoading: observationsLoading } = useQuery({
    queryKey: ['fact-observations', factId],
    queryFn: () => apiGetFactObservations(factId),
    enabled: !!factId,
  });

  const allData: Record<string, unknown>[] = factData?.data ?? [];
  const taskInfo = factData?.task_info;
  const sourceFile = factData?.source_file;
  const revisions = revisionsData?.items ?? [];
  const rawObservations = observationsData?.raw ?? [];
  const normalizedObservations = observationsData?.normalized ?? [];

  // 通用表格列：从所有行的 key 并集提取，保持首次出现顺序
  const tableColumns: ColumnsType<Record<string, unknown>> = useMemo(() => {
    const keySet = new Set<string>();
    const orderedKeys: string[] = [];
    for (const row of allData) {
      for (const key of Object.keys(row)) {
        if (!keySet.has(key)) {
          keySet.add(key);
          orderedKeys.push(key);
        }
      }
    }
    return orderedKeys.map((key) => ({
      title: key,
      dataIndex: key,
      key,
      ellipsis: true,
      render: (val: unknown) => {
        if (val === null || val === undefined) return '-';
        if (typeof val === 'number') return val;
        return String(val);
      },
    }));
  }, [allData]);

  // 观测数据表格列 — raw
  const rawObsColumns: ColumnsType<typeof rawObservations[number]> = useMemo(() => [
    {
      title: '来源路径',
      dataIndex: 'source_path',
      key: 'source_path',
      ellipsis: true,
      render: (val: string) => <span className="ocean-tech" style={{ fontSize: 12 }}>{val}</span>,
    },
    {
      title: '来源值',
      dataIndex: 'source_value',
      key: 'source_value',
      width: 140,
      render: (val: string) => <span className="ocean-tabular-number">{val}</span>,
    },
    {
      title: '单位',
      dataIndex: 'source_unit',
      key: 'source_unit',
      width: 80,
      render: (val: string | null) => val ?? <Text type="secondary">-</Text>,
    },
    {
      title: '来源名称',
      dataIndex: 'source_name',
      key: 'source_name',
      width: 160,
      render: (val: string | null) => val ?? <Text type="secondary">-</Text>,
    },
  ], []);

  // 观测数据表格列 — normalized
  const normalizedObsColumns: ColumnsType<typeof normalizedObservations[number]> = useMemo(() => [
    {
      title: '变量版本 ID',
      dataIndex: 'variable_version_id',
      key: 'variable_version_id',
      ellipsis: true,
      render: (val: string) => <span className="ocean-tech" style={{ fontSize: 12 }}>{val}</span>,
    },
    {
      title: '值',
      dataIndex: 'value',
      key: 'value',
      width: 140,
      render: (val: string) => <span className="ocean-tabular-number">{val}</span>,
    },
    {
      title: '单位',
      dataIndex: 'unit',
      key: 'unit',
      width: 80,
      render: (val: string | null) => val ?? <Text type="secondary">-</Text>,
    },
  ], []);

  // ---- 加载中 ----
  if (factLoading) {
    return (
      <div>
        <PageIntro index="FACTS / DETAIL" title="事实详情" />
        <FeedbackState kind="loading" title="正在加载事实详情..." rows={6} />
      </div>
    );
  }

  // ---- 查询错误（不显示空数据消息，显示重试） ----
  if (factError || !fact) {
    const errorDetail = factErr instanceof Error ? factErr.message : '事实详情获取失败';
    return (
      <div>
        <PageIntro index="FACTS / DETAIL" title="事实详情" />
        <FeedbackState
          kind="error"
          title="事实详情获取失败"
          description={errorDetail}
          onRetry={() => void refetchFact()}
        />
      </div>
    );
  }

  const statusTone = factStatusTone(fact.status);

  return (
    <div>
      <PageIntro
        index="FACTS / DETAIL"
        title="事实详情"
        actions={
          <Button
            onClick={() => void navigate({ to: '/lab-ops', search: { tab: 'facts' } })}
          >
            返回列表
          </Button>
        }
      />

      {/* 事实标识英雄区 */}
      <DataHero
        label="事实标识"
        value={<span className="ocean-tech">{fact.fact_id}</span>}
        summary={<StatusMark tone={statusTone} label={fact.status} />}
      />

      {/* 事实元数据 */}
      <DetailSection title="事实元数据">
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="事实 ID">
            <span className="ocean-tech">{fact.fact_id}</span>
          </Descriptions.Item>
          <Descriptions.Item label="修订号">
            <span className="ocean-tabular-number">r{fact.revision}</span>
          </Descriptions.Item>
          <Descriptions.Item label="事实类型">
            <Tag color="blue">{fact.fact_type}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <StatusMark tone={statusTone} label={fact.status} />
          </Descriptions.Item>
          <Descriptions.Item label="数据 ID">
            <span className="ocean-tech">{fact.subject_id}</span>
          </Descriptions.Item>
          <Descriptions.Item label="修订 ID">
            <span className="ocean-tech">{fact.revision_id}</span>
          </Descriptions.Item>
        </Descriptions>

        {/* 任务信息 */}
        {taskInfo && (
          <Descriptions title="导入来源" bordered column={2} size="small" style={{ marginTop: 16 }}>
            <Descriptions.Item label="任务名称">
              {taskInfo.task_name ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="执行人">
              {taskInfo.operator ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="项目名称">
              {taskInfo.project_name ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="所属单位">
              {taskInfo.department_name
                ? <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{taskInfo.department_name}</Tag>
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="数据来源" span={2}>
              {taskInfo.data_source_list && taskInfo.data_source_list.length > 0
                ? taskInfo.data_source_list.map((ds, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
                      <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                        {ds.component_display_name ?? ds.component}
                      </Tag>
                      {ds.object_name && (
                        <>
                          <span style={{ color: '#999', fontSize: 12 }}>&#10142;</span>
                          <Tag color="green" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                            {ds.object_name}
                          </Tag>
                        </>
                      )}
                      {ds.equipment_name && (
                        <>
                          <span style={{ color: '#999', fontSize: 12 }}>&#10142;</span>
                          <Tag color="cyan" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                            {ds.equipment_name}
                          </Tag>
                        </>
                      )}
                    </div>
                  ))
                : (taskInfo.data_interface ?? '-')}
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">
              <span className="ocean-tabular-number">{fmtTime(taskInfo.created_at)}</span>
            </Descriptions.Item>
            <Descriptions.Item label="原始数据文件">
              {sourceFile ? (
                <a
                  style={{ cursor: 'pointer' }}
                  onClick={async (e) => {
                    e.preventDefault();
                    try {
                      const url = await apiGetArtifactDownloadUrl(sourceFile.artifact_id);
                      window.open(url, '_blank');
                    } catch {
                      message.error('下载失败');
                    }
                  }}
                >
                  {sourceFile.filename}
                </a>
              ) : (
                <Text type="secondary">-</Text>
              )}
            </Descriptions.Item>
          </Descriptions>
        )}
      </DetailSection>

      {/* 版本历史 */}
      <DetailSection title="版本历史">
        {revisionsLoading ? (
          <FeedbackState kind="loading" title="正在加载版本历史..." rows={3} />
        ) : revisions.length === 0 ? (
          <Text type="secondary">暂无版本历史</Text>
        ) : (
          <Table
            dataSource={revisions}
            rowKey="revision_id"
            size="small"
            pagination={false}
            columns={[
              {
                title: '修订号',
                dataIndex: 'revision',
                key: 'revision',
                width: 100,
                render: (val: number) => <span className="ocean-tabular-number">r{val}</span>,
              },
              {
                title: '修订 ID',
                dataIndex: 'revision_id',
                key: 'revision_id',
                ellipsis: true,
                render: (val: string) => <span className="ocean-tech" style={{ fontSize: 12 }}>{val}</span>,
              },
              {
                title: '事实类型',
                dataIndex: 'fact_type',
                key: 'fact_type',
                width: 140,
                render: (val: string) => <Tag color="blue">{val}</Tag>,
              },
              {
                title: '数据 ID',
                dataIndex: 'subject_id',
                key: 'subject_id',
                ellipsis: true,
                render: (val: string) => <span className="ocean-tech" style={{ fontSize: 12 }}>{val}</span>,
              },
              {
                title: '状态',
                dataIndex: 'status',
                key: 'status',
                width: 120,
                render: (val: string) => <StatusMark tone={factStatusTone(val)} label={val} />,
              },
            ]}
          />
        )}
      </DetailSection>

      {/* 观测数据 */}
      <DetailSection title="观测数据">
        {observationsLoading ? (
          <FeedbackState kind="loading" title="正在加载观测数据..." rows={4} />
        ) : rawObservations.length === 0 && normalizedObservations.length === 0 ? (
          <Text type="secondary">暂无观测数据</Text>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {rawObservations.length > 0 && (
              <div>
                <Typography.Title level={5} style={{ marginBottom: 8 }}>原始观测</Typography.Title>
                <Table
                  columns={rawObsColumns}
                  dataSource={rawObservations}
                  rowKey="id"
                  size="small"
                  pagination={false}
                  scroll={{ y: 300 }}
                />
              </div>
            )}
            {normalizedObservations.length > 0 && (
              <div>
                <Typography.Title level={5} style={{ marginBottom: 8 }}>归一化观测</Typography.Title>
                <Table
                  columns={normalizedObsColumns}
                  dataSource={normalizedObservations}
                  rowKey="id"
                  size="small"
                  pagination={false}
                  scroll={{ y: 300 }}
                />
              </div>
            )}
          </div>
        )}
      </DetailSection>

      {/* 原始数据 */}
      <DetailSection
        title="原始数据"
        technical
        extra={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Radio.Group
              size="small"
              value={viewMode}
              onChange={(e) => setViewMode(e.target.value)}
              optionType="button"
              buttonStyle="solid"
            >
              <Radio.Button value="table">表格</Radio.Button>
              <Radio.Button value="json">原始</Radio.Button>
            </Radio.Group>
            <Button
              size="small"
              onClick={() => {
                const fullData = {
                  metadata: factData?.metadata ?? {},
                  data: allData,
                };
                const blob = new Blob([JSON.stringify(fullData, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `fact-${fact.fact_id.slice(0, 8)}.json`;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              导出
            </Button>
          </div>
        }
      >
        {dataError && allData.length === 0 ? (
          <FeedbackState
            kind="error"
            title="原始数据加载失败"
            onRetry={() => void refetchData()}
          />
        ) : viewMode === 'table' && allData.length > 0 ? (
          <Table<Record<string, unknown>>
            columns={tableColumns}
            dataSource={allData}
            rowKey={(_, idx) => String(idx)}
            size="small"
            pagination={false}
            scroll={{ y: 540 }}
          />
        ) : allData.length > 0 ? (
          <pre
            className="ocean-tech"
            style={{
              padding: 12,
              borderRadius: 6,
              fontSize: 13,
              maxHeight: 600,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              margin: 0,
            }}
          >
            {JSON.stringify({ metadata: factData?.metadata ?? {}, data: allData }, null, 2)}
          </pre>
        ) : (
          <Text type="secondary">暂无数据</Text>
        )}
      </DetailSection>
    </div>
  );
}
