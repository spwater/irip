/**
 * ResearchDataHeader — 数据首屏，显示快照状态和版本信息。
 *
 * 三种状态：
 * - 无快照：显示"先载入实验数据"提示
 * - 有快照：显示快照版本、证据数、冻结时间
 * - 快照过期：暖色提示但允许继续使用旧快照
 */
import { Card, Typography, Tag, Empty } from 'antd';
import { DatabaseOutlined } from '@ant-design/icons';

const { Text } = Typography;

interface Props {
  workspaceId: string;
  snapshotNumber: number | null;
  evidenceCount: number;
  onEvidenceChanged: () => void;
}

export function ResearchDataHeader({ snapshotNumber, evidenceCount }: Props) {
  if (snapshotNumber == null || snapshotNumber === 0) {
    return (
      <Card style={{ marginBottom: 16 }}>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <span>
              先载入实验数据并确认快照，AI 将自动推荐研究问题
            </span>
          }
        >
          <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
            在下方数据面板中添加 Fact，然后点击"冻结快照"
          </Text>
        </Empty>
      </Card>
    );
  }

  return (
    <Card style={{ marginBottom: 16 }} size="small">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <DatabaseOutlined style={{ fontSize: 18, color: 'var(--ocean-accent-primary)' }} />
          <div>
            <Text strong>数据快照 v{snapshotNumber}</Text>
            <div style={{ fontSize: 12, color: 'var(--ocean-text-muted)' }}>
              {evidenceCount} 条证据引用
            </div>
          </div>
          <Tag color="cyan">当前快照</Tag>
        </div>
      </div>
    </Card>
  );
}
