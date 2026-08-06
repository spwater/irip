/**
 * 工作空间卡片组件
 *
 * 显示名称、主研究问题摘要（截断）、数据数量、更新时间。
 * 点击跳转 WorkspaceDetail。
 */
import { Card, Tag } from 'antd';
import type { Workspace } from '@/api/research';

interface WorkspaceCardProps {
  workspace: Workspace;
  onClick: () => void;
}

export function WorkspaceCard({ workspace, onClick }: WorkspaceCardProps): JSX.Element {
  const statusLabel = workspace.status === 'draft' ? '活跃' : '已归档';
  const statusColor = workspace.status === 'draft' ? 'processing' : 'default';

  return (
    <Card
      hoverable
      size="small"
      onClick={onClick}
      title={
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {workspace.name}
        </span>
      }
      extra={<Tag color={statusColor}>{statusLabel}</Tag>}
    >
      <div style={{ fontSize: 13, color: 'var(--ocean-text-muted)', marginBottom: 8 }}>
        问题版本 v{workspace.current_question_version}
      </div>
      {workspace.forked_from_id && (
        <div style={{ fontSize: 12, color: 'var(--ocean-text-muted)' }}>
          分叉自其他工作空间
        </div>
      )}
    </Card>
  );
}
