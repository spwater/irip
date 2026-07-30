/**
 * 页面 Header 上下文 — 让子页面向 AppShell Header 注册内容。
 *
 * 每个页面通过 usePageHeader 设置自己的 index/title/tabs/actions，
 * AppShell 的 Header 读取这些值动态渲染。
 *
 * M-09 整改：
 * - 新增 usePageHeaderRegistration hook，支持成对注册/注销。
 * - 组件 mount 时注册 header，unmount 时清空 header，
 *   避免跨路由导航后旧标题和回调残留。
 */
import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from 'react';

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

/**
 * 成对注册/注销页面 Header（M-09）。
 *
 * - mount/update 时调用 setHeader(state)
 * - unmount 时调用 setHeader({}) 清空，避免跨路由残留旧标题和回调
 *
 * 使用方式：
 *   usePageHeaderRegistration({ index: '...', title: '...', tabs }, [activeTab])
 *
 * 第二个参数为额外依赖数组（如 activeTab），变化时重新注册。
 */
export function usePageHeaderRegistration(
  state: PageHeaderState,
  deps: React.DependencyList = [],
): void {
  const { setHeader } = usePageHeader();
  // 使用 ref 保存最新的 setHeader，确保 cleanup 调用的是同一个稳定引用
  const setHeaderRef = useRef(setHeader);
  setHeaderRef.current = setHeader;

  useEffect(() => {
    setHeaderRef.current(state);
    return () => {
      // unmount 时清空 header，防止跨路由残留
      setHeaderRef.current({});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
