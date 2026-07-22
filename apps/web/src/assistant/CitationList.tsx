import { List, Tag, Typography } from 'antd';
import { useNavigate } from '@tanstack/react-router';
import type { Citation } from '@/api/client';

const { Text, Link } = Typography;

/**
 * 引用类型 → 颜色映射
 */
const CITATION_COLOR: Record<string, string> = {
  parameter_version: 'purple',
  fact_revision: 'cyan',
  derivation_run: 'geekblue',
  model_version: 'magenta',
  standard_variable: 'gold',
};

/**
 * 引用类型 → 中文标签
 */
const CITATION_LABEL: Record<string, string> = {
  parameter_version: '参数版本',
  fact_revision: '事实修订',
  derivation_run: '推导运行',
  model_version: '模型版本',
  standard_variable: '标准变量',
};

/**
 * 引用列表组件
 *
 * 展示 AI 回答的引用来源，可点击跳转到对应对象详情页。
 */
export function CitationList({
  citations,
}: {
  citations: Citation[];
}): JSX.Element | null {
  const navigate = useNavigate();

  if (!citations || citations.length === 0) {
    return null;
  }

  const handleClick = (href: string): void => {
    // href 为前端路由路径（如 /parameters/xxx）
    void navigate({ to: href });
  };

  return (
    <div style={{ marginTop: 8 }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        引用来源：
      </Text>
      <List
        size="small"
        dataSource={citations}
        renderItem={(citation: Citation) => (
          <List.Item style={{ padding: '4px 0', border: 'none' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Tag color={CITATION_COLOR[citation.object_type] ?? 'default'}>
                {CITATION_LABEL[citation.object_type] ?? citation.object_type}
              </Tag>
              <Text style={{ fontSize: 13 }}>{citation.label}</Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                {citation.version}
              </Text>
              <Link
                style={{ fontSize: 12 }}
                onClick={() => handleClick(citation.href)}
              >
                查看详情 →
              </Link>
            </div>
          </List.Item>
        )}
        style={{ marginTop: 4 }}
      />
    </div>
  );
}

export default CitationList;
