import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PageIntro } from './PageIntro';
import { DataHero } from './DataHero';
import { MetricStrip } from './MetricStrip';
import { StatusMark } from './StatusMark';

describe('core UI primitives', () => {
  it('renders one page heading and a real action slot', () => {
    render(
      <PageIntro
        index="LAB / 01"
        title="实验室建设"
        description="建设实验要素"
        actions={<button>新建设备</button>}
      />,
    );
    expect(screen.getByRole('heading', { level: 1, name: '实验室建设' })).toBeVisible();
    expect(screen.getByRole('button', { name: '新建设备' })).toBeVisible();
  });

  it('renders tabular hero numbers with an explicit unit', () => {
    render(<DataHero label="当前流程" value="24" unit="条" />);
    expect(screen.getByText('24')).toHaveClass('ocean-tabular-number');
    expect(screen.getByText('条')).toBeVisible();
  });

  it('renders a non-color status marker', () => {
    render(<StatusMark tone="danger" label="失败" />);
    expect(screen.getByText('失败').parentElement).toHaveAttribute('data-marker', 'cross');
  });

  it('keeps metric source notes available to users', () => {
    render(
      <MetricStrip
        items={[{ key: 'facts', label: '最近事实', value: 18, note: '当前接口返回' }]}
      />,
    );
    expect(screen.getByText('当前接口返回')).toBeVisible();
  });
});
