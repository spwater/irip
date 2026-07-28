import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { OceanBackdrop } from './OceanBackdrop';
import { ContentFrame } from './ContentFrame';

describe('layout primitives', () => {
  it('renders a decorative background outside semantic content', () => {
    render(
      <OceanBackdrop>
        <main>研究数据</main>
      </OceanBackdrop>,
    );
    expect(screen.getByTestId('ocean-atmosphere')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByRole('main')).toHaveTextContent('研究数据');
  });

  it('exposes the selected width contract', () => {
    render(<ContentFrame width="wide">内容</ContentFrame>);
    expect(screen.getByText('内容')).toHaveAttribute('data-width', 'wide');
  });
});
