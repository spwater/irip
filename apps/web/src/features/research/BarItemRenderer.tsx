/**
 * BarItemRenderer — 结论栏中单条目渲染。
 *
 * 按 block_type 分支渲染：
 * - echarts / chart_ref → ChartBlock（optionStr=JSON.stringify(content_snapshot)）
 * - structured → StructuredConclusionDisplay
 * - table → AntD Table（columns/rows）
 * - text → Typography.Text
 */
import { Typography, Table } from 'antd';
import { ChartBlock } from '@/features/assistant/message-thread/components/ChartBlock';
import type { BarItem } from '@/api/researchConclusionBar';
import { StructuredConclusionDisplay } from './ConclusionLibrary';

const { Text } = Typography;

interface Props {
  item: BarItem;
}

export function BarItemRenderer({ item }: Props): JSX.Element {
  const { block_type: blockType, content_snapshot: snapshot } = item;

  if (blockType === 'echarts' || blockType === 'chart_ref') {
    return (
      <div style={{ zoom: 0.5, transformOrigin: 'top left' }}>
        <ChartBlock optionStr={JSON.stringify(snapshot)} />
      </div>
    );
  }

  if (blockType === 'structured') {
    return (
      <div style={{ zoom: 0.5, transformOrigin: 'top left' }}>
        <StructuredConclusionDisplay data={snapshot} />
      </div>
    );
  }

  if (blockType === 'table') {
    const columns = (snapshot.columns as string[]) ?? [];
    const rows = (snapshot.rows as unknown[][]) ?? [];
    if (columns.length === 0 && rows.length === 0) {
      return <Text type="secondary">{'（空表格）'}</Text>;
    }
    return (
      <div style={{ zoom: 0.5, transformOrigin: 'top left' }}>
        <Table
          size="small"
          pagination={rows.length > 20 ? { pageSize: 10, size: 'small' as const } : false}
          dataSource={rows.map((row, i) => {
            const rowObj: Record<string, unknown> = { key: i };
            columns.forEach((col, ci) => {
              rowObj[col] = row[ci];
            });
            return rowObj;
          })}
          columns={columns.map((col, ci) => ({
            title: col,
            dataIndex: col,
            key: ci,
          }))}
          scroll={{ x: true }}
        />
      </div>
    );
  }

  // text
  const textVal =
    typeof snapshot.text === 'string'
      ? snapshot.text
      : typeof snapshot === 'string'
        ? snapshot
        : '';
  return <Text>{textVal}</Text>;
}

export default BarItemRenderer;
