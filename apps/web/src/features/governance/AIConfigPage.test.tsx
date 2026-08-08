import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http } from '@/api/client';
import { AIConfigPage } from './AIConfigPage';

vi.mock('@/api/client', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  setAccessToken: vi.fn(),
  getAccessToken: vi.fn(() => null),
}));

const mockConfig = {
  base_url: 'https://api.openai.com/v1',
  api_key_masked: 'sk-x***abcd',
  model_name: 'gpt-4o',
  assistant_model_name: 'qwen-plus',
  research_model_name: 'deepseek-chat',
  enabled: true,
  meta_prompt: '你是一个有用的助手',
  model_thinking_enabled: false,
  assistant_thinking_enabled: true,
  research_thinking_enabled: false,
  updated_at: '2024-01-01T00:00:00Z',
};

function renderWithClient(ui: React.ReactElement): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>{ui}</AntApp>
    </QueryClientProvider>,
  );
}

describe('AIConfigPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(http.get).mockResolvedValue({ data: mockConfig });
  });

  it('renders 大模型配置 title', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText('大模型配置')).toBeInTheDocument();
  });

  it('renders API 地址 label', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText('API 地址')).toBeInTheDocument();
  });

  it('renders API 密钥 label', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText('API 密钥')).toBeInTheDocument();
  });

  it('renders 数据提取模型 label', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText('数据提取模型')).toBeInTheDocument();
  });

  it('renders AI助手模型 label', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText('AI助手模型')).toBeInTheDocument();
  });

  it('renders 研发助手模型 label', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText('研发助手模型')).toBeInTheDocument();
  });

  it('renders 保存大模型配置 button', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText('保存大模型配置')).toBeInTheDocument();
  });

  it('renders 提示词 section title', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText('数据接口推荐-系统提示词')).toBeInTheDocument();
  });

  it('renders 保存提示词 button', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText('保存提示词')).toBeInTheDocument();
  });

  it('prefills form with config data', async () => {
    renderWithClient(<AIConfigPage />);
    await screen.findByText('大模型配置');
    await waitFor(() => {
      expect(screen.getByDisplayValue('https://api.openai.com/v1')).toBeInTheDocument();
      expect(screen.getByDisplayValue('gpt-4o')).toBeInTheDocument();
    });
  });

  it('renders 测试 buttons for each model', async () => {
    renderWithClient(<AIConfigPage />);
    await screen.findByText('大模型配置');
    const testButtons = screen.getAllByRole('button', { name: /测\s*试/ });
    expect(testButtons).toHaveLength(3);
  });

  it('renders description paragraph for prompt section', async () => {
    renderWithClient(<AIConfigPage />);
    expect(await screen.findByText(/用于「提示词推荐」功能的大模型系统提示词/)).toBeInTheDocument();
  });

  it('prefills prompt textarea with existing meta_prompt', async () => {
    renderWithClient(<AIConfigPage />);
    await screen.findByText('数据接口推荐-系统提示词');
    await waitFor(() => {
      expect(screen.getByDisplayValue('你是一个有用的助手')).toBeInTheDocument();
    });
  });
});
