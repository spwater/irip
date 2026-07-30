import { create } from 'zustand';
import { apiGetJob, type JobSummary, type JobStatus } from '@/api/client';
import { registerCleanupCallback } from '@/auth/sessionState';

/**
 * 作业状态管理（zustand store）
 * - job ID 列表持久化到 localStorage（只存 ID，不存状态）
 * - 权威状态从 API 刷新
 *
 * H-15: localStorage key 含 tenant+user，实现跨账号隔离
 * - 即使 clearSessionState 未执行，不同账号的 key 也天然隔离
 * - 加载失败时清旧数据，避免残留
 */

/**
 * H-15: 动态 localStorage key（含 tenant+user）
 * 初始值为无 scope 的 fallback key，用户登录后通过 setJobStoreScope 更新
 */
let currentStorageKey: string = 'irip:jobs';

/**
 * H-15: 设置当前作业 store 的存储范围
 * 在用户登录/refresh 成功后调用，传入 organization_id 和 user_id
 */
export function setJobStoreScope(tenant: string, user: string): void {
  currentStorageKey = `irip:${tenant}:${user}:jobs`;
}

/** 终态：不需要继续轮询 */
const TERMINAL_STATUSES: JobStatus[] = ['succeeded', 'failed', 'cancelled'];

/** 活跃状态：drawer 应自动打开 */
const ACTIVE_STATUSES: JobStatus[] = [
  'accepted',
  'queued',
  'running',
  'retry_wait',
  'cancel_requested',
];

function loadJobIds(): string[] {
  try {
    const raw = localStorage.getItem(currentStorageKey);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === 'string');
  } catch {
    return [];
  }
}

function saveJobIds(ids: string[]): void {
  try {
    localStorage.setItem(currentStorageKey, JSON.stringify(ids));
  } catch {
    // localStorage 不可用时忽略
  }
}

interface JobStoreState {
  /** 当前已知的作业列表（从 API 获取权威状态） */
  jobs: JobSummary[];
  /** 是否正在加载 */
  loading: boolean;
  /** 抽屉是否打开 */
  drawerOpen: boolean;
  /** 从 localStorage 恢复作业 ID 并从 API 刷新状态 */
  loadJobs: () => Promise<void>;
  /** 添加作业 ID */
  addJob: (id: string) => void;
  /** 移除作业 ID */
  removeJob: (id: string) => void;
  /** 设置抽屉开关 */
  setDrawerOpen: (open: boolean) => void;
  /** 重置状态：清内存 + 清当前 scope 的 localStorage */
  reset: () => void;
}

export const useJobStore = create<JobStoreState>((set, get) => ({
  jobs: [],
  loading: false,
  drawerOpen: false,

  loadJobs: async (): Promise<void> => {
    const ids = loadJobIds();
    if (ids.length === 0) {
      set({ jobs: [], loading: false });
      return;
    }
    set({ loading: true });
    try {
      const jobs = await Promise.all(ids.map((id) => apiGetJob(id)));
      const hasActive = jobs.some((j) => ACTIVE_STATUSES.includes(j.status));
      set({ jobs, loading: false, drawerOpen: hasActive || get().drawerOpen });
    } catch {
      // H-15: 加载失败时清旧数据，避免跨账号残留
      set({ jobs: [], loading: false });
    }
  },

  addJob: (id: string): void => {
    const ids = loadJobIds();
    if (!ids.includes(id)) {
      ids.push(id);
      saveJobIds(ids);
    }
    void get().loadJobs();
  },

  removeJob: (id: string): void => {
    const ids = loadJobIds().filter((i) => i !== id);
    saveJobIds(ids);
    set((state) => ({
      jobs: state.jobs.filter((j) => j.id !== id),
    }));
  },

  setDrawerOpen: (open: boolean): void => {
    set({ drawerOpen: open });
  },

  reset: (): void => {
    // H-15: 清除当前 scope 的 localStorage，避免跨账号残留
    try {
      localStorage.removeItem(currentStorageKey);
    } catch {
      // localStorage 不可用时忽略
    }
    // 重置 scope 到默认值
    currentStorageKey = 'irip:jobs';
    set({ jobs: [], loading: false, drawerOpen: false });
  },
}));

// H-15: 注册清理回调，供 clearSessionState() 调用
registerCleanupCallback(() => useJobStore.getState().reset());

export { TERMINAL_STATUSES, ACTIVE_STATUSES };
