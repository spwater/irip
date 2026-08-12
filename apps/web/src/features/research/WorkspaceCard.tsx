/**
 * WorkspaceCard — 工作空间卡片
 *
 * Timeline refactoring: 显示快照数、轮次数和活跃状态，不再显示问题版本。
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
      <div style={{ fontSize: 13, color: 'var(--ocean-text-muted)', marginBottom: 4 }}>
        {workspace.latest_snapshot_number != null
          ? `快照 v${workspace.latest_snapshot_number}`
          : '无快照'}
        {workspace.turn_count > 0 && ` · ${workspace.turn_count} 轮研究`}
      </div>
      {workspace.active_run_status && (
        <Tag color="processing" style={{ fontSize: 12 }}>
          运行中
        </Tag>
      )}
    </Card>
  );
}
