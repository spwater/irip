import {
  Button,
  Card,
  Descriptions,
  Empty,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import type { ColumnsType } from 'antd/es/table';
import {
  apiGetFact,
  apiGetFactObservations,
  apiListFactRevisions,
  type FactRevision,
  type RawObservation,
  type NormalizedObservation,
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

/** 合并 raw + normalized 的行类型 */
type MergedObservation = {
  key: string;
  source_path: string;
  source_value: string;
  source_unit: string | null;
  source_name: string | null;
  normalized_value: string | null;
  normalized_unit: string | null;
  variable_version_id: string | null;
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

  const { data: observations } = useQuery({
    queryKey: ['fact-observations', factId],
    queryFn: () => apiGetFactObservations(factId),
    enabled: !!factId,
  });

  // ---- 合并 raw + normalized 观察值 ----
  const mergedRows: MergedObservation[] = (() => {
    if (!observations) return [];
    const raws: RawObservation[] = observations.raw;
    const norms: NormalizedObservation[] = observations.normalized;
    // 建立 raw_id → normalized 映射
    const normMap = new Map<string, NormalizedObservation>();
    for (const n of norms) {
      normMap.set(n.raw_observation_id, n);
    }
    return raws.map((r) => {
      const n = normMap.get(r.id);
      return {
        key: r.id,
        source_path: r.source_path,
        source_value: r.source_value,
        source_unit: r.source_unit,
        source_name: r.source_name,
        normalized_value: n ? n.value : null,
        normalized_unit: n ? n.unit : null,
        variable_version_id: n ? n.variable_version_id : null,
      };
    });
  })();

  // ---- 观察值表格列 ----
  const observationColumns: ColumnsType<MergedObservation> = [
    {
      title: '来源路径',
      dataIndex: 'source_path',
      key: 'source_path',
      width: 140,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '来源值',
      dataIndex: 'source_value',
      key: 'source_value',
      width: 120,
      render: (v: string) => <Text strong>{v}</Text>,
    },
    {
      title: '来源单位',
      dataIndex: 'source_unit',
      key: 'source_unit',
      width: 80,
      render: (u: string | null) => u ?? '-',
    },
    {
      title: '标准化值',
      dataIndex: 'normalized_value',
      key: 'normalized_value',
      width: 120,
      render: (v: string | null) => v ?? <Text type="secondary">-</Text>,
    },
    {
      title: '标准化单位',
      dataIndex: 'normalized_unit',
      key: 'normalized_unit',
      width: 80,
      render: (u: string | null) => u ?? '-',
    },
    {
      title: '变量版本ID',
      dataIndex: 'variable_version_id',
      key: 'variable_version_id',
      width: 200,
      ellipsis: true,
      render: (v: string | null) =>
        v ? <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>{v.slice(0, 8)}...</Text> : <Text type="secondary">-</Text>,
    },
  ];

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
        onClick={() => void navigate({ to: '/facts' })}
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

      {/* 观测数据（raw + normalized 合并展示） */}
      <Card title={`观测数据（${mergedRows.length} 条）`}>
        <Table<MergedObservation>
          columns={observationColumns}
          dataSource={mergedRows}
          pagination={false}
          size="small"
          scroll={{ x: true }}
        />
        {mergedRows.length === 0 && (
          <Text type="secondary">暂无观测数据</Text>
        )}
      </Card>
    </div>
  );
}
