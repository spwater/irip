/**
 * ResultCard — 成果包列表卡片
 *
 * 显示标题/摘要/发布者/时间/版本号/产物数量/权限标识
 */
import { Card, Tag, Space, Typography } from 'antd';
import {
  DatabaseOutlined,
  BarChartOutlined,
  BulbOutlined,
  StarFilled,
  StarOutlined,
  LockOutlined,
  GlobalOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { SearchResultItem } from '@/api/researchPublish';

const { Text } = Typography;

export type ResultCardProps = {
  item: SearchResultItem;
  isFavorited?: boolean;
  onClick: () => void;
  onFavoriteToggle?: () => void;
};

const ACL_LABELS: Record<string, { label: string; color: string; icon: JSX.Element }> = {
  private: { label: '私有', color: 'default', icon: <LockOutlined /> },
  tree: { label: '部门', color: 'blue', icon: <TeamOutlined /> },
  explicit: { label: '指定用户', color: 'orange', icon: <UserOutlined /> },
  all: { label: '公开', color: 'green', icon: <GlobalOutlined /> },
};

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  } catch {
    return iso;
  }
}

export function ResultCard({
  item,
  isFavorited,
  onClick,
  onFavoriteToggle,
}: ResultCardProps): JSX.Element {
  const aclInfo = ACL_LABELS[item.current_acl_type] ?? ACL_LABELS.private;

  return (
    <Card
      hoverable
      size="small"
      style={{
        borderRadius: 8,
        border: '1px solid var(--ocean-border-subtle, #e8e8e8)',
        transition: 'all 0.3s ease',
      }}
      onClick={onClick}
      title={
        <Space size="small" style={{ flex: 1, minWidth: 0 }}>
          <Text
            strong
            style={{ fontSize: 14 }}
            ellipsis={{ tooltip: item.title || item.name }}
          >
            {item.title || item.name}
          </Text>
          <Tag color="blue" style={{ margin: 0, fontSize: 10 }}>
            v{item.current_version}
          </Tag>
        </Space>
      }
      extra={
        <Space size={4} onClick={(e) => e.stopPropagation()}>
          <Tag color={aclInfo.color} style={{ margin: 0, fontSize: 11 }}>
            {aclInfo.icon} {aclInfo.label}
          </Tag>
          {onFavoriteToggle && (
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
              style={{ cursor: 'pointer', fontSize: 14, color: isFavorited ? '#faad14' : '#bfbfbf' }}
            >
              {isFavorited ? <StarFilled /> : <StarOutlined />}
            </span>
          )}
        </Space>
      }
    >
      {/* 分析问题（从 summary 解析） */}
      {(() => {
        let questions: string[] = [];
        try {
          const parsed = JSON.parse(item.summary || '');
          if (parsed?.metadata?.analysis_questions) {
            questions = parsed.metadata.analysis_questions;
          }
        } catch { /* not JSON */ }
        return questions.length > 0 ? (
          <div style={{ marginBottom: 8 }}>
            {questions.map((q, i) => (
              <Text key={i} type="secondary" style={{ fontSize: 12, display: 'block' }}>
                {i + 1}. {q}
              </Text>
            ))}
          </div>
        ) : null;
      })()}

      {/* 底部信息 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontSize: 12,
          color: 'var(--ocean-text-muted, #8c8c8c)',
        }}
      >
        <Space size={12}>
          <span><DatabaseOutlined /> {item.dataset_count}</span>
          <span><BarChartOutlined /> {item.view_count}</span>
          <span><BulbOutlined /> {item.insight_count}</span>
        </Space>
        <span>{formatTime(item.published_at)}</span>
      </div>
    </Card>
  );
}
