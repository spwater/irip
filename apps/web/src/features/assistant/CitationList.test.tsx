import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import type { Citation } from '@/api/models-ai';
import { CitationList } from './CitationList';

const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}));

function renderWithApp(ui: React.ReactElement): ReturnType<typeof render> {
  return render(<AntApp>{ui}</AntApp>);
}

const citations: Citation[] = [
  {
    object_type: 'parameter_version',
    object_id: 'pv-001',
    version: 'v1.0',
    label: '烧结温度参数',
    href: '/parameters/pv-001',
  },
  {
    object_type: 'fact_revision',
    object_id: 'fr-002',
    version: 'v2.0',
    label: 'XRD 实验数据',
    href: '/facts/fr-002',
  },
  {
    object_type: 'model_version',
    object_id: 'mv-003',
    version: 'v3.0',
    label: '预测模型',
    href: '/models/mv-003',
  },
];

describe('CitationList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns null when citations is empty', () => {
    const { container } = render(<CitationList citations={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders 引用来源 label', () => {
    renderWithApp(<CitationList citations={citations} />);
    expect(screen.getByText('引用来源：')).toBeInTheDocument();
  });

  it('renders all citation labels', () => {
    renderWithApp(<CitationList citations={citations} />);
    expect(screen.getByText('烧结温度参数')).toBeInTheDocument();
    expect(screen.getByText('XRD 实验数据')).toBeInTheDocument();
    expect(screen.getByText('预测模型')).toBeInTheDocument();
  });

  it('renders citation version text', () => {
    renderWithApp(<CitationList citations={citations} />);
    expect(screen.getByText('v1.0')).toBeInTheDocument();
    expect(screen.getByText('v2.0')).toBeInTheDocument();
  });

  it('renders 查看详情 link for each citation', () => {
    renderWithApp(<CitationList citations={citations} />);
    const links = screen.getAllByText('查看详情 →');
    expect(links).toHaveLength(3);
  });

  it('renders type tags with correct labels', () => {
    renderWithApp(<CitationList citations={citations} />);
    expect(screen.getByText('参数版本')).toBeInTheDocument();
    expect(screen.getByText('事实修订')).toBeInTheDocument();
    expect(screen.getByText('模型版本')).toBeInTheDocument();
  });

  it('navigates when 查看详情 clicked', async () => {
    renderWithApp(<CitationList citations={citations} />);
    const links = screen.getAllByText('查看详情 →');
    await userEvent.click(links[0]);
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/parameters/pv-001' });
  });

  it('uses object_type as fallback label for unknown types', () => {
    const unknownCitation: Citation = {
      object_type: 'custom_type',
      object_id: 'x-001',
      version: 'v1',
      label: '自定义',
      href: '/custom/x-001',
    };
    renderWithApp(<CitationList citations={[unknownCitation]} />);
    expect(screen.getByText('custom_type')).toBeInTheDocument();
  });

  it('handles single citation', () => {
    renderWithApp(<CitationList citations={[citations[0]]} />);
    expect(screen.getByText('烧结温度参数')).toBeInTheDocument();
    expect(screen.getAllByText('查看详情 →')).toHaveLength(1);
  });
});
