/**
 * 对话搜索输入框组件。
 *
 * 左栏对话列表顶部搜索框，输入关键词后 debounce 300ms 触发搜索回调，
 * 清空时恢复完整列表。使用 Ant Design Input + SearchOutlined 图标。
 */
import { Input } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { useEffect, useRef, useState } from 'react';

export function ConversationSearch({
  onSearch,
}: {
  /** 搜索关键词变更回调（debounce 300ms，空字符串表示清空搜索） */
  onSearch: (keyword: string) => void;
}): JSX.Element {
  const [value, setValue] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // debounce 300ms 后触发搜索
  useEffect(() => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      onSearch(value.trim());
    }, 300);
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, [value, onSearch]);

  return (
    <Input
      prefix={<SearchOutlined style={{ color: 'var(--ocean-text-muted)' }} />}
      placeholder="搜索对话标题或内容..."
      value={value}
      onChange={(e) => setValue(e.target.value)}
      allowClear
      size="small"
      style={{ margin: '8px 12px', width: 'calc(100% - 24px)' }}
    />
  );
}

export default ConversationSearch;
