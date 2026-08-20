/**
 * ResultDetailView — 成果包详情页
 *
 * 布局参考 FactDetail.tsx：Row gutter=16, Col span=10/14
 * 左侧：发布数据来源（Descriptions 表格）
 * 右侧：发布数据详情（Tabs: 元数据/单点数据/序列数据）
 */
import { useCallback, useState } from 'react';
import {
  Row,
  Col,
  Card,
  Tag,
  Space,
  Typography,
  Button,
  Tabs,
  Table,
  Descriptions,
  message,
  Input,
  Popconfirm,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ArrowLeftOutlined,
  EditOutlined,
  StarFilled,
  StarOutlined,
} from '@ant-design/icons';
import type { ResultDetail, ResultVersionDetail } from '@/api/researchPublish';
import {
  apiUpdateResultMetadata,
  apiWithdrawResult,
} from '@/api/researchPublish';
import { tryParseStructured } from './ConclusionLibrary';

const { Text } = Typography;

export type ResultDetailViewProps = {
  resultId: string;
  detail: ResultDetail;
  isFavorited: boolean;
  onBack: () => void;
  onFavoriteToggle: () => void;
  workspaceId?: string;
};


export function ResultDetailView({
  resultId,
  detail,
  isFavorited,
  onBack,
  onFavoriteToggle,
  workspaceId,
}: ResultDetailViewProps): JSX.Element {
  const resultRef = detail.result;
  const currentVersion = detail.current_version;

  const [versionDetail] = useState<ResultVersionDetail | null>(
    currentVersion ?? null,
  );
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(resultRef.name);
  const [savingName, setSavingName] = useState(false);

  // 保存名称编辑
  const handleSaveName = useCallback(async () => {
    if (!editName.trim()) {
      message.warning('名称不能为空');
      return;
    }
    if (!workspaceId) {
      message.info('仅在 Workspace 内可编辑');
      setEditing(false);
      return;
    }
    setSavingName(true);
    try {
      await apiUpdateResultMetadata(workspaceId, resultId, { name: editName.trim() });
      message.success('已保存');
      setEditing(false);
    } catch {
      message.error('保存失败');
    } finally {
      setSavingName(false);
    }
  }, [workspaceId, resultId, editName]);

  // 撤回成果
  const [withdrawing, setWithdrawing] = useState(false);
  const handleWithdraw = useCallback(async () => {
    setWithdrawing(true);
    try {
      await apiWithdrawResult(resultId);
      message.success('已撤回');
      window.location.reload();
    } catch {
      message.error('撤回失败');
    } finally {
      setWithdrawing(false);
    }
  }, [resultId]);

  // 解析结构化数据
  const structured = versionDetail?.summary
    ? tryParseStructured(versionDetail.summary)
    : null;
  const metadata = (structured?.metadata as Record<string, unknown> | undefined) ?? {};
  const points = (structured?.points as Array<Record<string, unknown>> | undefined) ?? [];
  const seriesList = (structured?.series as Array<Record<string, unknown>> | undefined) ?? [];

  // 单点数据表格列
  const pointColumns: ColumnsType<Record<string, unknown>> = [
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

  return (
    <div>
      {/* 顶部导航栏 */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Button icon={<ArrowLeftOutlined />} type="text" onClick={onBack}>
          返回列表
        </Button>
        {editing ? (
          <Space>
            <Input
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              style={{ width: 240 }}
              placeholder="成果包名称"
            />
            <Button size="small" type="primary" loading={savingName} onClick={handleSaveName}>
              保存
            </Button>
            <Button size="small" onClick={() => { setEditing(false); setEditName(resultRef.name); }}>
              取消
            </Button>
          </Space>
        ) : (
          <>
            <Text strong style={{ fontSize: 16 }}>{resultRef.name}</Text>
            {workspaceId && (
              <Button
                size="small"
                type="text"
                icon={<EditOutlined />}
                onClick={() => setEditing(true)}
              />
            )}
          </>
        )}
        <Tag color={resultRef.status === 'published' ? 'green' : 'default'}>
          {resultRef.status === 'published' ? '已发布' : resultRef.status}
        </Tag>
        <Tag color="blue">v{resultRef.current_version}</Tag>
        <span
          role="button"
          tabIndex={0}
          onClick={onFavoriteToggle}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onFavoriteToggle();
            }
          }}
          style={{ cursor: 'pointer', fontSize: 16, color: isFavorited ? '#faad14' : '#bfbfbf' }}
        >
          {isFavorited ? <StarFilled /> : <StarOutlined />}
        </span>
        <div style={{ marginLeft: 'auto' }}>
          {resultRef.status === 'published' && (
            <Popconfirm
              title="确认撤回此成果包？"
              description="撤回后其他用户将无法看到此成果，数据保留可重新发布。"
              onConfirm={handleWithdraw}
              okText="撤回"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button size="small" danger loading={withdrawing}>
                撤回
              </Button>
            </Popconfirm>
          )}
        </div>
      </div>

      <Row gutter={16}>
        {/* 左侧：发布数据来源 */}
        <Col span={10}>
          <Card size="small" title="发布数据来源">
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="成果 ID">
                <Text copyable code style={{ fontSize: 12 }}>
                  {resultId}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={resultRef.status === 'published' ? 'green' : 'default'}>
                  {resultRef.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="来源轮次">
                {Array.isArray(metadata.source_turns) && (metadata.source_turns as number[]).length > 0
                  ? (metadata.source_turns as number[]).map((t) => (
                      <Tag key={t} color="blue" style={{ margin: 2 }}>#{t}</Tag>
                    ))
                  : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="分析问题">
                {Array.isArray(metadata.analysis_questions) && (metadata.analysis_questions as string[]).length > 0
                  ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {(metadata.analysis_questions as string[]).map((q, i) => (
                        <Text key={i} style={{ fontSize: 12 }}>{i + 1}. {q}</Text>
                      ))}
                    </div>
                  )
                  : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="发布时间">
                {versionDetail?.published_at
                  ? new Date(versionDetail.published_at).toLocaleString('zh-CN', { hour12: false })
                  : '-'}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        {/* 右侧：发布数据详情 */}
        <Col span={14}>
          <Card
            size="small"
            title={`发布数据详情（${points.length} 个指标，${seriesList.length} 组序列）`}
          >
            <Tabs
              defaultActiveKey="metadata"
              size="small"
              items={[
                {
                  key: 'metadata',
                  label: '元数据',
                  children: Object.keys(metadata).length > 0 ? (
                    <Descriptions bordered column={1} size="small">
                      {Object.entries(metadata).map(([k, v]) => (
                        <Descriptions.Item key={k} label={k}>
                          {v === null || v === undefined ? '-'
                            : Array.isArray(v) ? v.join(', ')
                            : typeof v === 'object' ? JSON.stringify(v)
                            : String(v)}
                        </Descriptions.Item>
                      ))}
                    </Descriptions>
                  ) : (
                    <Text type="secondary">暂无元数据</Text>
                  ),
                },
                {
                  key: 'points',
                  label: `单点数据（${points.length}）`,
                  children: points.length > 0 ? (
                    <Table
                      columns={pointColumns}
                      dataSource={points}
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
                    seriesList.map((s, i) => {
                      const name = (s.name as string) ?? `序列 ${i + 1}`;
                      const columns = (s.columns as string[]) ?? [];
                      const rows = (s.rows as unknown[][]) ?? [];
                      return (
                        <Card key={i} size="small" title={name} style={{ marginBottom: 12 }}>
                          <Table
                            size="small"
                            pagination={false}
                            rowKey={(_, idx) => String(idx)}
                            dataSource={rows.map((row, ri) => {
                              const obj: Record<string, unknown> = { _key: ri };
                              columns.forEach((c, ci) => { obj[c] = row[ci]; });
                              return obj;
                            })}
                            columns={columns.map((c) => ({
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
                      );
                    })
                  ) : (
                    <Text type="secondary">暂无序列数据</Text>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
