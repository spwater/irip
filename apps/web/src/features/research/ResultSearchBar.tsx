/**
 * ResultSearchBar — 成果包搜索栏
 *
 * 关键词搜索 + 筛选器（发布者/时间/数据类型/标签）+ 语义搜索切换（P1）
 */
import { Input, Select, DatePicker, Button, Space, Tooltip, Switch } from 'antd';
import { SearchOutlined, ReloadOutlined } from '@ant-design/icons';
import { useCallback } from 'react';

const { RangePicker } = DatePicker;

export type SearchResultFilters = {
  query: string;
  tags: string;
  date_from: string | null;
  date_to: string | null;
  data_type: string | null;
  semantic: boolean;
};

export type ResultSearchBarProps = {
  filters: SearchResultFilters;
  onChange: (filters: SearchResultFilters) => void;
  onSearch: () => void;
  onReset: () => void;
};

export function ResultSearchBar({
  filters,
  onChange,
  onSearch,
  onReset,
}: ResultSearchBarProps): JSX.Element {
  const handleQueryChange = useCallback(
    (val: string) => onChange({ ...filters, query: val }),
    [filters, onChange],
  );

  const handleTagsChange = useCallback(
    (val: string) => onChange({ ...filters, tags: val }),
    [filters, onChange],
  );

  const handleDateChange = useCallback(
    (_: unknown, dateStrings: [string, string]) => {
      onChange({
        ...filters,
        date_from: dateStrings[0] || null,
        date_to: dateStrings[1] || null,
      });
    },
    [filters, onChange],
  );

  const handleDataTypeChange = useCallback(
    (val: string | null) => onChange({ ...filters, data_type: val ?? null }),
    [filters, onChange],
  );

  const handleSemanticToggle = useCallback(
    (checked: boolean) => onChange({ ...filters, semantic: checked }),
    [filters, onChange],
  );

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 8,
        alignItems: 'center',
        padding: '12px 16px',
        background: 'var(--ocean-surface-structural, rgba(255,255,255,0.6))',
        borderRadius: 8,
        border: '1px solid var(--ocean-border-subtle, #e8e8e8)',
      }}
    >
      <Input
        prefix={<SearchOutlined />}
        placeholder="搜索成果包标题、摘要或标签…"
        value={filters.query}
        onChange={(e) => handleQueryChange(e.target.value)}
        onPressEnter={onSearch}
        style={{ width: 280 }}
        allowClear
      />
      <Input
        placeholder="标签筛选（逗号分隔）"
        value={filters.tags}
        onChange={(e) => handleTagsChange(e.target.value)}
        onPressEnter={onSearch}
        style={{ width: 180 }}
        allowClear
      />
      <RangePicker
        onChange={handleDateChange}
        style={{ width: 240 }}
      />
      <Select
        placeholder="数据类型"
        value={filters.data_type}
        onChange={handleDataTypeChange}
        allowClear
        style={{ width: 140 }}
        options={[
          { label: '衍生数据集', value: 'derived_dataset' },
          { label: '视图', value: 'view' },
          { label: 'Insight', value: 'insight' },
        ]}
      />
      <Tooltip title="语义搜索（基于向量相似度排序）">
        <Space size={4}>
          <span style={{ fontSize: 12, color: 'var(--ocean-text-muted, #8c8c8c)' }}>语义</span>
          <Switch size="small" checked={filters.semantic} onChange={handleSemanticToggle} />
        </Space>
      </Tooltip>
      <Space style={{ marginLeft: 'auto' }}>
        <Button type="primary" icon={<SearchOutlined />} onClick={onSearch}>
          搜索
        </Button>
        <Button icon={<ReloadOutlined />} onClick={onReset}>
          重置
        </Button>
      </Space>
    </div>
  );
}
