/**
 * Root 数据量统计卡片（P1-T1-05）
 *
 * 显示 root 部门归属的各主要数据表（fact/parameter/model/flow_definition/flow_run/equipment）的行数。
 * 调用 GET /api/v1/governance/root-data-stats 端点。
 */
import { Card, Col, Row, Statistic, Typography, Alert } from 'antd';
import { DatabaseOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { apiGetRootDataStats } from '@/api/governance';
import { useAuthStore } from '@/features/auth/AuthProvider';

const { Text } = Typography;

/** 表名 → 颜色映射 */
const TABLE_COLORS: Record<string, string> = {
  fact: '#1677ff',
  parameter: '#52c41a',
  model: '#722ed1',
  flow_definition: '#fa8c16',
  flow_run: '#13c2c2',
  equipment: '#eb2f96',
};

export function RootDataStats(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.roles?.includes('platform_administrator') ?? false;

  const { data, isLoading, isError } = useQuery({
    queryKey: ['root-data-stats'],
    queryFn: apiGetRootDataStats,
    enabled: isAdmin,
    staleTime: 60_000,
  });

  if (!isAdmin) {
    return (
      <Card title="Root 数据量统计">
        <Text type="secondary">仅平台管理员可查看此统计。</Text>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card title="Root 数据量统计">
        <Alert type="error" message="数据加载失败" description="请检查后端服务是否正常。" />
      </Card>
    );
  }

  const stats = data?.stats ?? [];
  const totalRows = stats.reduce((sum, s) => sum + s.count, 0);

  return (
    <Card
      title={
        <span>
          <DatabaseOutlined style={{ marginRight: 8 }} />
          Root 数据量统计
          {data && (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 12, fontWeight: 'normal' }}>
              部门：{data.root_department_name}
            </Text>
          )}
        </span>
      }
      loading={isLoading}
    >
      <Alert
        type="info"
        showIcon
        message="公共数据归属监控"
        description="以下统计显示 root（公共）部门归属的数据量。如果公共数据过多，建议通过「数据移交」工具迁移到具体实验室。"
        style={{ marginBottom: 16 }}
      />
      <Row gutter={[16, 16]}>
        {stats.map((s) => (
          <Col key={s.table} xs={12} sm={8} md={6} lg={4}>
            <Statistic
              title={s.display_name}
              value={s.count}
              suffix="行"
              valueStyle={{ color: TABLE_COLORS[s.table] ?? '#1677ff' }}
            />
          </Col>
        ))}
        <Col xs={12} sm={8} md={6} lg={4}>
          <Statistic
            title="合计"
            value={totalRows}
            suffix="行"
            valueStyle={{ fontWeight: 'bold' }}
          />
        </Col>
      </Row>
    </Card>
  );
}
