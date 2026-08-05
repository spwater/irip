import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MentionInput } from './MentionInput';
import type { MentionableUser } from '@/api/collaboration';

const mockUsers: MentionableUser[] = [
  { id: 'u-001', display_name: '张三', avatar_url: null, roles: ['lab_member'] },
  { id: 'u-002', display_name: '李四', avatar_url: null, roles: ['lab_director'] },
  { id: 'u-003', display_name: '王五', avatar_url: null, roles: ['lab_viewer'] },
];

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

/** 模拟在 textarea 中输入文本（触发 handleChange）。 */
function typeInto(textarea: HTMLTextAreaElement, value: string): void {
  fireEvent.change(textarea, { target: { value, selectionStart: value.length } });
}

/** 受控包装器：维护 value + mentions 状态，模拟父组件行为。 */
function StatefulMentionInput({
  onMentionsChangeSpy,
  onChangeSpy,
  initialValue = '',
  initialMentions = [] as string[],
  participants = mockUsers.map((u) => ({
    user_id: u.id,
    display_name: u.display_name,
    avatar_url: u.avatar_url,
  })),
}: {
  onMentionsChangeSpy?: (m: string[]) => void;
  onChangeSpy?: (v: string) => void;
  initialValue?: string;
  initialMentions?: string[];
  participants?: Array<{ user_id: string; display_name: string; avatar_url: string | null }>;
}): React.ReactElement {
  const [value, setValue] = useState(initialValue);
  const [mentions, setMentions] = useState(initialMentions);
  return (
    <MentionInput
      value={value}
      onChange={(v) => {
        setValue(v);
        onChangeSpy?.(v);
      }}
      mentions={mentions}
      onMentionsChange={(m) => {
        setMentions(m);
        onMentionsChangeSpy?.(m);
      }}
      participants={participants}
    />
  );
}

describe('MentionInput', () => {
  it('renders a textarea with placeholder', () => {
    renderWithClient(
      <MentionInput
        value=""
        onChange={() => {}}
        mentions={[]}
        onMentionsChange={() => {}}
      />,
    );
    expect(
      screen.getByPlaceholderText('输入问题，@ 提及成员，Enter 发送'),
    ).toBeInTheDocument();
  });

  it('displays current value in textarea', () => {
    renderWithClient(
      <MentionInput
        value="分析D50数据"
        onChange={() => {}}
        mentions={[]}
        onMentionsChange={() => {}}
      />,
    );
    expect(screen.getByDisplayValue('分析D50数据')).toBeInTheDocument();
  });

  it('calls onChange when user types text', () => {
    const onChange = vi.fn();
    renderWithClient(
      <MentionInput value="" onChange={onChange} mentions={[]} onMentionsChange={() => {}} />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    typeInto(textarea, '新内容');
    expect(onChange).toHaveBeenCalledWith('新内容');
  });

  it('shows filtered user dropdown when @ is typed', async () => {
    renderWithClient(<StatefulMentionInput />);
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    typeInto(textarea, '@');
    await waitFor(() => {
      expect(screen.getByText('张三')).toBeInTheDocument();
      expect(screen.getByText('李四')).toBeInTheDocument();
      expect(screen.getByText('王五')).toBeInTheDocument();
    });
  });

  it('filters users by name when typing @ + partial name', async () => {
    renderWithClient(<StatefulMentionInput />);
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    typeInto(textarea, '@张');
    await waitFor(() => {
      expect(screen.getByText('张三')).toBeInTheDocument();
      expect(screen.queryByText('李四')).not.toBeInTheDocument();
      expect(screen.queryByText('王五')).not.toBeInTheDocument();
    });
  });

  it('inserts @displayName and calls onMentionsChange when user is selected', async () => {
    const onMentionsChangeSpy = vi.fn();
    const onChangeSpy = vi.fn();
    renderWithClient(
      <StatefulMentionInput
        onMentionsChangeSpy={onMentionsChangeSpy}
        onChangeSpy={onChangeSpy}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    typeInto(textarea, '@');
    const zhangsan = await screen.findByText('张三');
    fireEvent.click(zhangsan);
    // onChangeSpy 的最后一次调用应包含 @张三
    const calls = onChangeSpy.mock.calls;
    const lastValue = calls[calls.length - 1][0] as string;
    expect(lastValue).toContain('@张三');
    // onMentionsChange 应包含 u-001
    expect(onMentionsChangeSpy).toHaveBeenCalledWith(['u-001']);
  });

  it('removes mention from list when @displayName is deleted from text', async () => {
    const onMentionsChangeSpy = vi.fn();
    renderWithClient(
      <StatefulMentionInput
        onMentionsChangeSpy={onMentionsChangeSpy}
        initialMentions={['u-001']}
      />,
    );
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
    // 先输入含 @张三 的文本（handleChange 检测到 @张三 → 保留 mention）
    typeInto(textarea, '@张三 分析数据');
    // 再替换为不含 @张三 的文本
    typeInto(textarea, '分析数据');
    // mentions 应被清空
    expect(onMentionsChangeSpy).toHaveBeenCalledWith([]);
  });
});
