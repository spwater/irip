import { Collapse, Tag, Typography } from 'antd';
import type { ToolCallSummary } from '@/api/client';

const { Text, Paragraph } = Typography;

/**
 * 工具状态 → 颜色映射
 */
const STATUS_COLOR: Record<string, string> = {
  executed: 'green',
  candidate: 'orange',
  rejected: 'red',
  forbidden: 'volcano',
};

/**
 * 工具状态 → 中文标签
 */
const STATUS_LABEL: Record<string, string> = {
  executed: '已执行',
  candidate: '候选（需审批）',
  rejected: '已拒绝',
  forbidden: '权限不足',
};

/**
 * 工具调用轨迹组件
 *
 * 展示 AI 调用了哪些工具、传入了什么参数、结果摘要与执行状态。
 * 使用 Collapse 折叠面板，每项一个工具调用。
 */
export function ToolTrace({
  toolCalls,
}: {
  toolCalls: ToolCallSummary[];
}): JSX.Element | null {
  if (!toolCalls || toolCalls.length === 0) {
    return null;
  }

  const items = toolCalls.map((tc: ToolCallSummary, idx: number) => ({
    key: String(idx),
    label: (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Text code style={{ fontSize: 13 }}>
          {tc.tool}
        </Text>
        <Tag color={STATUS_COLOR[tc.status] ?? 'default'}>
          {STATUS_LABEL[tc.status] ?? tc.status}
        </Tag>
        <Text type="secondary" style={{ fontSize: 12, flex: 1 }}>
          {tc.summary}
        </Text>
      </div>
    ),
    children: (
      <div>
        <Text type="secondary" style={{ fontSize: 12 }}>
          参数：
        </Text>
        <Paragraph style={{ margin: '4px 0 0 0' }}>
          <pre
            style={{
              margin: 0,
              padding: 8,
              background: 'var(--ocean-surface-structural)',
              borderRadius: 4,
              fontSize: 12,
              overflowX: 'auto',
            }}
          >
            {JSON.stringify(tc.args, null, 2)}
          </pre>
        </Paragraph>
      </div>
    ),
  }));

  return (
    <div style={{ marginTop: 8 }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        工具调用轨迹：
      </Text>
      <Collapse
        size="small"
        items={items}
        style={{ marginTop: 4 }}
        defaultActiveKey={[]}
      />
    </div>
  );
}

export default ToolTrace;
