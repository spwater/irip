/**
 * 页面 Header 上下文 — 让子页面向 AppShell Header 注册内容。
 *
 * 每个页面通过 usePageHeader 设置自己的 index/title/tabs/actions，
 * AppShell 的 Header 读取这些值动态渲染。
 */

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

export interface PageHeaderTabsItem {
  key: string;
  label: string;
}

export interface PageHeaderState {
  index?: string;
  title?: string;
  tabs?: PageHeaderTabsItem[];
  activeTab?: string;
  onTabChange?: (key: string) => void;
  actions?: ReactNode;
  /** 大标题模式：无渐变线、无 Tab 占位，标题居中填满 Header 高度 */
  heroTitle?: boolean;
}

interface PageHeaderContextValue {
  header: PageHeaderState;
  setHeader: (state: PageHeaderState) => void;
}

const PageHeaderContext = createContext<PageHeaderContextValue | null>(null);

export function PageHeaderProvider({ children }: { children: ReactNode }): JSX.Element {
  const [header, setHeaderState] = useState<PageHeaderState>({});
  const setHeader = useCallback((state: PageHeaderState) => {
    setHeaderState(state);
  }, []);
  return (
    <PageHeaderContext.Provider value={{ header, setHeader }}>
      {children}
    </PageHeaderContext.Provider>
  );
}

export function usePageHeader(): {
  header: PageHeaderState;
  setHeader: (state: PageHeaderState) => void;
} {
  const ctx = useContext(PageHeaderContext);
  if (!ctx) {
    return { header: {}, setHeader: () => {} };
  }
  return ctx;
}
