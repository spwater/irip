/**
 * KnowledgeSearchStatus — 知识库检索覆盖声明组件
 *
 * 在右栏 AI 助手覆盖声明区新增"知识库检索"状态。
 * 状态取值：
 *   ✅ 已检索（N 篇文献）
 *   ⚠ 降级（知识库不可用）
 *   — 不适用
 *
 * 参照 PRD 4.4 节 UI 设计与 arch-research-lineage.md 2.3 节文件。
 */
import { Space, Tag, Typography, Tooltip } from 'antd';
import {
  CheckCircleOutlined,
  WarningOutlined,
  MinusOutlined,
  BookOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

// ============================================================
// Props
// ============================================================

export type KnowledgeSearchStatusProps = {
  /** 检索状态 */
  status: 'searched' | 'degraded' | 'not_applicable';
  /** 已检索到的文献数（status=searched 时显示） */
  referenceCount?: number;
  /** 降级原因（status=degraded 时显示） */
  degradeReason?: string;
  /** 使用的 Provider 名称 */
  providerName?: string;
};

// ============================================================
// 组件
// ============================================================

/**
 * KnowledgeSearchStatus — 知识库检索覆盖声明
 *
 * 在覆盖声明区展示知识库检索状态，帮助用户理解分析过程中
 * 是否使用了知识库文献以及降级情况。
 */
export function KnowledgeSearchStatus({
  status,
  referenceCount = 0,
  degradeReason,
  providerName,
}: KnowledgeSearchStatusProps): JSX.Element {
  const renderStatus = () => {
    switch (status) {
      case 'searched':
        return (
          <Tag color="green" icon={<CheckCircleOutlined />}>
            已检索 ({referenceCount} 篇文献)
          </Tag>
        );
      case 'degraded':
        return (
          <Tooltip
            title={degradeReason ?? '知识库不可用，已降级为仅数据分析'}
          >
            <Tag color="orange" icon={<WarningOutlined />}>
              降级（知识库不可用）
            </Tag>
          </Tooltip>
        );
      case 'not_applicable':
      default:
        return (
          <Tag icon={<MinusOutlined />}>
            不适用
          </Tag>
        );
    }
  };

  return (
    <Space size={4} style={{ fontSize: 12 }}>
      <BookOutlined style={{ color: 'var(--ocean-text-muted, #6f8d9c)' }} />
      <Text type="secondary" style={{ fontSize: 12 }}>
        知识库检索:
      </Text>
      {renderStatus()}
      {providerName && status === 'searched' && (
        <Text style={{ fontSize: 10, color: 'var(--ocean-text-muted, #6f8d9c)' }}>
          ({providerName})
        </Text>
      )}
    </Space>
  );
}
