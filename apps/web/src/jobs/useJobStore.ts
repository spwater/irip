import { create } from 'zustand';
import { apiGetJob, type JobSummary, type JobStatus } from '@/api/client';

/**
 * 作业状态管理（zustand store）
 * - job ID 列表持久化到 localStorage（只存 ID，不存状态）
 * - 权威状态从 API 刷新
 */

const STORAGE_KEY = 'irip.job_ids';

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
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === 'string');
  } catch {
    return [];
  }
}

function saveJobIds(ids: string[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
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
  /** 重置状态（测试用） */
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
      set({ loading: false });
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
    set({ jobs: [], loading: false, drawerOpen: false });
  },
}));

export { TERMINAL_STATUSES, ACTIVE_STATUSES };
