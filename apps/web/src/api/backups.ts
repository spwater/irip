/**
 * 数据库备份 API 客户端
 *
 * 对应后端 apps/api/routers/backups.py：
 * - apiListBackups: GET /backups — 列出备份记录（按 type/status 过滤）
 * - apiGetBackupDetail: GET /backups/{id} — 备份详情
 * - apiCreateBackup: POST /backups — 创建备份作业（daily / milestone）
 * - apiRestoreBackup: POST /backups/{id}/restore — 从备份恢复
 * - apiDeleteBackup: DELETE /backups/{id} — 删除里程碑备份
 * - apiGetBackupStats: GET /backups/stats — 汇总统计
 *
 * 复用 apps/web/src/api/client.ts 的 http axios 实例。
 */

import { http } from './client';

// ============================================================
// 类型定义
// ============================================================

/** 备份类型 */
export type BackupType = 'daily' | 'milestone' | 'pre_restore';

/** 备份状态 */
export type BackupStatus = 'pending' | 'succeeded' | 'failed';

/** 备份方法 */
export type BackupMethod = 'pitr' | 'pg_dump';

/** 备份记录项 */
export type BackupRecordItem = {
  id: string;
  job_id: string | null;
  backup_type: BackupType;
  name: string | null;
  description: string | null;
  backup_date: string | null;
  file_path: string;
  file_size: number | null;
  sha256: string | null;
  status: BackupStatus;
  migration_version: string | null;
  application_version: string | null;
  created_by: string | null;
  created_at: string;
  completed_at: string | null;
  expires_at: string | null;
  error_message: string | null;
  backup_method: BackupMethod | null;
  backup_timestamp: string | null;
};

/** 备份记录分页列表响应 */
export type BackupRecordListResponse = {
  items: BackupRecordItem[];
  next_cursor: string | null;
  has_more: boolean;
};

/** 创建备份请求体 */
export type CreateBackupBody = {
  type: 'daily' | 'milestone';
  name?: string;
  description?: string;
};

/** 创建备份响应 */
export type CreateBackupResponse = {
  job_id: string;
  backup_record_id: string;
  status: string;
  kind: string;
  created_at: string;
};

/** 恢复备份请求体 */
export type RestoreBackupBody = {
  skip_migrations?: boolean;
  recovery_target_time?: string;
};

/** 恢复备份响应 */
export type RestoreBackupResponse = {
  job_id: string;
  backup_id: string;
  status: string;
  kind: string;
  created_at: string;
};

/** 备份汇总统计 */
export type BackupStats = {
  total_count: number;
  total_size_bytes: number;
  daily_count: number;
  milestone_count: number;
  succeeded_count: number;
  failed_count: number;
};

// ============================================================
// API 函数
// ============================================================

/**
 * 列出备份记录（分页，支持按类型/状态过滤）
 */
export async function apiListBackups(
  params?: { type?: BackupType; status?: BackupStatus; cursor?: string; limit?: number },
): Promise<BackupRecordListResponse> {
  const res = await http.get<BackupRecordListResponse>('/backups/', { params });
  return res.data;
}

/**
 * 获取备份记录详情
 */
export async function apiGetBackupDetail(id: string): Promise<BackupRecordItem> {
  const res = await http.get<BackupRecordItem>(`/backups/${id}`);
  return res.data;
}

/**
 * 创建备份作业（daily 自动 / milestone 手动）
 */
export async function apiCreateBackup(body: CreateBackupBody): Promise<CreateBackupResponse> {
  const res = await http.post<CreateBackupResponse>('/backups/', body);
  return res.data;
}

/**
 * 从备份恢复（先创建 pre_restore 备份再执行恢复：v1=pg_restore，v2=PITR）
 */
export async function apiRestoreBackup(
  id: string,
  body?: RestoreBackupBody,
): Promise<RestoreBackupResponse> {
  const res = await http.post<RestoreBackupResponse>(`/backups/${id}/restore`, body ?? {});
  return res.data;
}

/**
 * 删除里程碑备份
 */
export async function apiDeleteBackup(id: string): Promise<void> {
  await http.delete(`/backups/${id}`);
}

/**
 * 获取备份汇总统计
 */
export async function apiGetBackupStats(): Promise<BackupStats> {
  const res = await http.get<BackupStats>('/backups/stats');
  return res.data;
}
