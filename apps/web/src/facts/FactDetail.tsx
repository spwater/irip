import { useMemo, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Radio,
  Row,
  Spin,
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
} from '@/api/client';

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

  const allData: Record<string, unknown>[] = factData?.data ?? [];
  const taskInfo = factData?.task_info;
  const sourceFile = factData?.source_file;

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

  if (factLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!fact) {
    return <Empty description="未找到数据" />;
  }

  return (
    <div>
      <Button
        onClick={() => void navigate({ to: '/lab-ops', search: { tab: 'facts' } })}
        style={{ marginBottom: 16 }}
      >
        返回列表
      </Button>

      <Row gutter={16}>
        {/* 左侧：导入数据来源 */}
        <Col span={10}>
          <Card title="导入数据来源">
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="任务名称">
                {taskInfo?.task_name ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="项目名称">
                {taskInfo?.project_name ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="当前数据ID">
                <Text code>{fact.subject_id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="数据来源">
                {taskInfo?.data_source_list && taskInfo.data_source_list.length > 0
                  ? taskInfo.data_source_list.map((ds, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
                        <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                          {ds.component}
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
                        {ds.department_name && (
                          <>
                            <span style={{ color: '#999', fontSize: 12 }}>&#10142;</span>
                            <Tag color="geekblue" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                              {ds.department_name}
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
          </Card>
        </Col>

        {/* 右侧：导入数据详情 */}
        <Col span={14}>
          <Card
            title={`导入数据详情（${allData.length} 条）`}
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
            {viewMode === 'table' && allData.length > 0 ? (
              <Table<Record<string, unknown>>
                columns={tableColumns}
                dataSource={allData}
                rowKey={(_, idx) => String(idx)}
                size="small"
                pagination={false}
                scroll={{ y: 540 }}
              />
            ) : (
              <pre
                style={{
                  background: '#f5f5f5',
                  padding: 12,
                  borderRadius: 6,
                  fontSize: 13,
                  fontFamily: 'monospace',
                  maxHeight: 600,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  margin: 0,
                }}
              >
                {JSON.stringify({ metadata: factData?.metadata ?? {}, data: allData }, null, 2)}
              </pre>
            )}
            {allData.length === 0 && (
              <Text type="secondary">暂无数据</Text>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
