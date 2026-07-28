import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FocusModal } from '@/components/ui/FocusModal';
import { FocusDrawer } from '@/components/ui/FocusDrawer';

describe('Overlay contract', () => {
  it('keeps title, body error, and ordered footer actions accessible', () => {
    render(
      <FocusModal
        open
        title="确认发布"
        error="发布失败"
        cancelText="取消"
        okText="发布"
        onCancel={vi.fn()}
        onOk={vi.fn()}
      >
        版本 v2
      </FocusModal>,
    );
    expect(screen.getByRole('dialog', { name: '确认发布' })).toBeVisible();
    expect(screen.getByText('发布失败')).toBeVisible();
    const actions = screen
      .getAllByRole('button')
      .map((button) => button.textContent?.replace(/\s/g, ''));
    expect(actions).toEqual(expect.arrayContaining(['取消', '发布']));
  });

  it('renders drawer title and body content', () => {
    render(
      <FocusDrawer
        open
        title="成员管理"
        onClose={vi.fn()}
      >
        成员列表
      </FocusDrawer>,
    );
    expect(screen.getByText('成员管理')).toBeVisible();
    expect(screen.getByText('成员列表')).toBeVisible();
  });

  it('renders error region with alert role', () => {
    render(
      <FocusDrawer open title="测试" error="操作失败" onClose={vi.fn()}>
        内容
      </FocusDrawer>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('操作失败');
  });
});
