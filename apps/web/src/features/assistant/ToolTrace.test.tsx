import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import type { ToolCallSummary } from '@/api/models-ai';
import { ToolTrace } from './ToolTrace';

function renderWithApp(ui: React.ReactElement): ReturnType<typeof render> {
  return render(<AntApp>{ui}</AntApp>);
}

const toolCalls: ToolCallSummary[] = [
  {
    tool: 'search_standards',
    args: { keyword: '烧结温度', limit: 10 },
    summary: '搜索到 5 条标准变量',
    status: 'executed',
  },
  {
    tool: 'query_facts',
    args: { subject_id: 'sample-001' },
    summary: '查询到 3 条实验数据',
    status: 'candidate',
  },
  {
    tool: 'delete_record',
    args: { id: 'rec-001' },
    summary: '需要审批后执行',
    status: 'rejected',
  },
];

describe('ToolTrace', () => {
  it('returns null when toolCalls is empty', () => {
    const { container } = render(<ToolTrace toolCalls={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('returns null when toolCalls is undefined', () => {
    const { container } = render(<ToolTrace toolCalls={undefined as unknown as ToolCallSummary[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders 工具调用轨迹 label', () => {
    renderWithApp(<ToolTrace toolCalls={toolCalls} />);
    expect(screen.getByText('工具调用轨迹：')).toBeInTheDocument();
  });

  it('renders tool names as code text', () => {
    renderWithApp(<ToolTrace toolCalls={toolCalls} />);
    expect(screen.getByText('search_standards')).toBeInTheDocument();
    expect(screen.getByText('query_facts')).toBeInTheDocument();
    expect(screen.getByText('delete_record')).toBeInTheDocument();
  });

  it('renders tool summaries', () => {
    renderWithApp(<ToolTrace toolCalls={toolCalls} />);
    expect(screen.getByText('搜索到 5 条标准变量')).toBeInTheDocument();
    expect(screen.getByText('查询到 3 条实验数据')).toBeInTheDocument();
  });

  it('renders status tags with correct labels', () => {
    renderWithApp(<ToolTrace toolCalls={toolCalls} />);
    expect(screen.getByText('已执行')).toBeInTheDocument();
    expect(screen.getByText('候选（需审批）')).toBeInTheDocument();
    expect(screen.getByText('已拒绝')).toBeInTheDocument();
  });

  it('renders unknown status as raw status text', () => {
    const unknownCall: ToolCallSummary = {
      tool: 'custom_tool',
      args: {},
      summary: 'custom',
      status: 'unknown_status',
    };
    renderWithApp(<ToolTrace toolCalls={[unknownCall]} />);
    expect(screen.getByText('unknown_status')).toBeInTheDocument();
  });

  it('expands panel to show args JSON', async () => {
    renderWithApp(<ToolTrace toolCalls={[toolCalls[0]]} />);
    // Click the collapse header to expand
    const header = screen.getByText('search_standards').closest('.ant-collapse-item')!;
    await userEvent.click(header.querySelector('.ant-collapse-header')!);
    // The args should be displayed as pre-formatted JSON
    expect(screen.getByText(/"keyword": "烧结温度"/)).toBeInTheDocument();
  });

  it('renders 参数 label inside expanded panel', async () => {
    renderWithApp(<ToolTrace toolCalls={[toolCalls[0]]} />);
    const header = screen.getByText('search_standards').closest('.ant-collapse-item')!;
    await userEvent.click(header.querySelector('.ant-collapse-header')!);
    expect(screen.getByText('参数：')).toBeInTheDocument();
  });

  it('handles single tool call', () => {
    renderWithApp(<ToolTrace toolCalls={[toolCalls[0]]} />);
    expect(screen.getByText('search_standards')).toBeInTheDocument();
    expect(screen.getByText('已执行')).toBeInTheDocument();
  });
});
