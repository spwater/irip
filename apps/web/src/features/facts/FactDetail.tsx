import { useState } from 'react';
import {
  Button,
  Card,
  Col,
  Descriptions,
  Radio,
  Row,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import { apiGetFact, apiGetFactData } from '@/api/facts-provenance';
import { apiGetArtifactDownloadUrl } from '@/api/models-ai';
import { PageIntro, DetailSection, FeedbackState } from '@/shared/ui';

const { Text } = Typography;

/** 把 UTC 时间字符串转成本地时间显示 */
function fmtTime(v: string | null | undefined): string {
  if (!v) return '-';
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString('zh-CN', { hour12: false });
}

/**
 * 实验数据详情页面
 */
export function FactDetail(): JSX.Element {
  const params = useParams({ strict: false });
  const factId = String((params as Record<string, unknown>).factId ?? '');
  const navigate = useNavigate();
  const [viewMode, setViewMode] = useState<'table' | 'json'>('table');

  const { data: fact, isLoading: factLoading } = useQuery({
    queryKey: ['fact', factId],
    queryFn: () => apiGetFact(factId),
    enabled: !!factId,
  });

  const { data: factData } = useQuery({
    queryKey: ['fact-data', factId],
    queryFn: () => apiGetFactData(factId),
    enabled: !!factId,
  });

  const metadata: Record<string, unknown> = factData?.metadata ?? {};
  const allPoints: { name: string; value: unknown; unit: string | null }[] = factData?.points ?? [];
  const seriesList: { name: string; columns: string[]; rows: unknown[][] }[] = factData?.series ?? [];
  const taskInfo = factData?.task_info;
  const sourceFile = factData?.source_file;

  // 单点数据表格列
  const pointColumns: ColumnsType<{ name: string; value: unknown; unit: string | null }> = [
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '值', dataIndex: 'value', key: 'value', ellipsis: true,
      render: (val: unknown) => {
        if (val === null || val === undefined) return '-';
        if (typeof val === 'number') return val;
        return String(val);
      },
    },
    {
      title: '单位', dataIndex: 'unit', key: 'unit', ellipsis: true,
      render: (val: unknown) => (val === null || val === undefined ? '-' : String(val)),
    },
  ];

  if (factLoading) {
    return <FeedbackState state="loading" title="加载事实详情…" style={{ padding: 48 }} />;
  }

  if (!fact) {
    return <FeedbackState state="empty" title="未找到数据" />;
  }

  return (
    <div className="ocean-page-enter">
      <PageIntro
        index="DETAIL / FACT"
        title="事实详情"
        subtitle="实验数据的来源信息与详细内容。"
        actions={
          <Button
            onClick={() => void navigate({ to: '/lab-ops', search: { tab: 'facts' } })}
          >
            返回列表
          </Button>
        }
      />

      <Row gutter={16}>
        {/* 左侧：导入数据来源 */}
        <Col span={10}>
          <DetailSection title="导入数据来源">
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="任务名称">
                {taskInfo?.task_name ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="负责人">
                {taskInfo?.operator ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="执行人">
                {taskInfo?.run_operator ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="测量设备">
                {taskInfo?.equipment_name
                  ? <Tag color="cyan" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{taskInfo.equipment_name}</Tag>
                  : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="项目名称">
                {taskInfo?.project_name ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="所属单位">
                {taskInfo?.department_name
                  ? <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>{taskInfo.department_name}</Tag>
                  : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="当前数据ID">
                <Text code>{fact.subject_id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="数据来源">
                {taskInfo?.data_source_list && taskInfo.data_source_list.length > 0
                  ? taskInfo.data_source_list.map((ds, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
                        <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                          {ds.component_display_name ?? ds.component}
                        </Tag>
                        {ds.object_name && (
                          <>
                            <span style={{ color: 'var(--ocean-text-muted)', fontSize: 12 }}>&#10142;</span>
                            <Tag color="green" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                              {ds.object_name}
                            </Tag>
                          </>
                        )}
                        {ds.equipment_name && (
                          <>
                            <span style={{ color: 'var(--ocean-text-muted)', fontSize: 12 }}>&#10142;</span>
                            <Tag color="cyan" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                              {ds.equipment_name}
                            </Tag>
                          </>
                        )}
                      </div>
                    ))
                  : (taskInfo?.data_interface ?? '-')}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {fmtTime(taskInfo?.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label="事实类型">
                <Tag color="blue">{fact.fact_type}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="原始数据">
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
                  '-'
                )}
              </Descriptions.Item>
            </Descriptions>
          </DetailSection>
        </Col>

        {/* 右侧：导入数据详情 */}
        <Col span={14}>
          <DetailSection
            title={`导入数据详情（${allPoints.length} 个指标，${seriesList.length} 组序列）`}
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
                      points: allPoints,
                      series: seriesList,
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
            {viewMode === 'table' ? (
              <Tabs
                defaultActiveKey="points"
                items={[
                  {
                    key: 'metadata',
                    label: '元数据',
                    children: Object.keys(metadata).length > 0 ? (
                      <Descriptions bordered column={1} size="small">
                        {Object.entries(metadata).map(([k, v]) => (
                          <Descriptions.Item key={k} label={k}>
                            {v === null || v === undefined ? '-' : String(v)}
                          </Descriptions.Item>
                        ))}
                      </Descriptions>
                    ) : (
                      <Text type="secondary">暂无元数据</Text>
                    ),
                  },
                  {
                    key: 'points',
                    label: `单点数据（${allPoints.length}）`,
                    children: allPoints.length > 0 ? (
                      <Table
                        columns={pointColumns}
                        dataSource={allPoints}
                        rowKey={(_, idx) => String(idx)}
                        size="small"
                        pagination={false}
                        scroll={{ y: 400 }}
                      />
                    ) : (
                      <Text type="secondary">暂无单点数据</Text>
                    ),
                  },
                  {
                    key: 'series',
                    label: `序列数据（${seriesList.length}）`,
                    children: seriesList.length > 0 ? (
                      seriesList.map((s, i) => (
                        <Card key={i} size="small" title={s.name ?? `序列 ${i + 1}`} style={{ marginBottom: 12 }}>
                          <Table
                            size="small"
                            pagination={false}
                            rowKey={(_, idx) => String(idx)}
                            dataSource={s.rows.map((r, ri) => {
                              const obj: Record<string, unknown> = { _key: ri };
                              (s.columns ?? []).forEach((c, ci) => { obj[c] = r[ci]; });
                              return obj;
                            })}
                            columns={(s.columns ?? []).map((c) => ({
                              title: c,
                              dataIndex: c,
                              key: c,
                              ellipsis: true,
                              render: (val: unknown) => {
                                if (val === null || val === undefined) return '-';
                                if (typeof val === 'number') return val;
                                return String(val);
                              },
                            }))}
                          />
                        </Card>
                      ))
                    ) : (
                      <Text type="secondary">暂无序列数据</Text>
                    ),
                  },
                ]}
              />
            ) : (
              <pre
                style={{
                  background: 'var(--ocean-surface-structural)',
                  padding: 12,
                  borderRadius: 6,
                  fontSize: 13,
                  fontFamily: 'var(--ocean-font-mono)',
                  maxHeight: 600,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  margin: 0,
                }}
              >
                {JSON.stringify({ metadata: factData?.metadata ?? {}, points: allPoints, series: seriesList }, null, 2)}
              </pre>
            )}
          </DetailSection>
        </Col>
      </Row>
    </div>
  );
}
