import {
  Button,
  Card,
  Descriptions,
  Empty,
  Spin,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import {
  apiGetFact,
  apiGetFactData,
  apiListFactRevisions,
  type FactRevision,
} from '@/api/client';

const { Text } = Typography;

/** 状态 → 颜色 */
const STATUS_COLOR: Record<string, string> = {
  active: 'green',
  superseded: 'orange',
  withdrawn: 'red',
};

/** 状态 → 中文标签 */
const STATUS_LABEL: Record<string, string> = {
  active: '活跃',
  superseded: '已替代',
  withdrawn: '已撤回',
};

/**
 * 事实详情页面
 *
 * 后端 API 返回结构：
 * - GET /facts/{id} → FactDetail（fact_id, revision, revision_id, fact_type, subject_id, status）
 * - GET /facts/{id}/revisions → { items: FactRevision[], next_cursor }
 * - GET /facts/{id}/observations → { raw: RawObservation[], normalized: NormalizedObservation[] }
 */
export function FactDetail(): JSX.Element {
  const params = useParams({ strict: false });
  const factId = String((params as Record<string, unknown>).factId ?? '');
  const navigate = useNavigate();

  // ---- 数据查询 ----
  const { data: fact, isLoading: factLoading } = useQuery({
    queryKey: ['fact', factId],
    queryFn: () => apiGetFact(factId),
    enabled: !!factId,
  });

  const { data: revisionsResp } = useQuery({
    queryKey: ['fact-revisions', factId],
    queryFn: () => apiListFactRevisions(factId),
    enabled: !!factId,
  });

  const { data: factData } = useQuery({
    queryKey: ['fact-data', factId],
    queryFn: () => apiGetFactData(factId),
    enabled: !!factId,
  });

  // ---- 提取干净的行数据 ----
  const allData: Record<string, unknown>[] = factData?.data ?? [];
  const artifactMetadata: Record<string, unknown> = factData?.metadata ?? {};

  // ---- 构建展示数据 ----
  const metadata = fact
    ? {
        fact_id: fact.fact_id,
        fact_type: fact.fact_type,
        subject_id: fact.subject_id,
        status: fact.status,
        revision: fact.revision,
        ...artifactMetadata,
      }
    : {};

  // ---- 加载与空状态 ----
  if (factLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!fact) {
    return <Empty description="未找到事实" />;
  }

  return (
    <div>
      <Button
        onClick={() => void navigate({ to: '/lab-ops', search: { tab: 'facts' } })}
        style={{ marginBottom: 16 }}
      >
        返回列表
      </Button>

      {/* 事实基本信息 */}
      <Card title="事实详情" style={{ marginBottom: 16 }}>
        <Descriptions bordered column={2}>
          <Descriptions.Item label="Fact ID">
            <Text copyable style={{ fontFamily: 'monospace', fontSize: 13 }}>
              {fact.fact_id}
            </Text>
          </Descriptions.Item>
          <Descriptions.Item label="事实类型">
            <Tag color="blue">{fact.fact_type}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="主体ID">
            <Text code>{fact.subject_id}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={STATUS_COLOR[fact.status] ?? 'default'}>
              {STATUS_LABEL[fact.status] ?? fact.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="修订号">第 {fact.revision} 版</Descriptions.Item>
          <Descriptions.Item label="修订ID">
            <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 13 }}>
              {fact.revision_id.slice(0, 8)}...
            </Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 修订历史 */}
      <Card title="修订历史" style={{ marginBottom: 16 }}>
        <Timeline
          items={(revisionsResp?.items ?? []).map((r: FactRevision) => ({
            color: r.status === 'active' ? 'green' : r.status === 'withdrawn' ? 'red' : 'orange',
            children: (
              <div>
                <Text strong>第 {r.revision} 版</Text>
                <br />
                <Tag color={STATUS_COLOR[r.status] ?? 'default'}>
                  {STATUS_LABEL[r.status] ?? r.status}
                </Tag>
                <br />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  主体: {r.subject_id}
                </Text>
              </div>
            ),
          }))}
        />
        {(revisionsResp?.items ?? []).length === 0 && (
          <Text type="secondary">暂无修订历史</Text>
        )}
      </Card>

      {/* Metadata */}
      <Card
        title="Metadata"
        style={{ marginBottom: 16 }}
        extra={
          <Button
            size="small"
            onClick={() => {
              const blob = new Blob([JSON.stringify(metadata, null, 2)], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `fact-${fact.fact_id.slice(0, 8)}-metadata.json`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            导出 JSON
          </Button>
        }
      >
        <pre
          style={{
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 6,
            fontSize: 13,
            fontFamily: 'monospace',
            maxHeight: 300,
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            margin: 0,
          }}
        >
          {JSON.stringify(metadata, null, 2)}
        </pre>
      </Card>

      {/* 全部数据 */}
      <Card
        title={`数据（${allData.length} 条）`}
        extra={
          <Button
            size="small"
            onClick={() => {
              const blob = new Blob([JSON.stringify(allData, null, 2)], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `fact-${fact.fact_id.slice(0, 8)}-data.json`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            导出 JSON
          </Button>
        }
      >
        <pre
          style={{
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 6,
            fontSize: 13,
            fontFamily: 'monospace',
            maxHeight: 500,
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            margin: 0,
          }}
        >
          {JSON.stringify(allData, null, 2)}
        </pre>
        {allData.length === 0 && (
          <Text type="secondary">暂无数据</Text>
        )}
      </Card>
    </div>
  );
}
