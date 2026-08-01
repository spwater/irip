/**
 * 备份 API 客户端类型定义测试（PITR 升级）
 *
 * 验证 apps/web/src/api/backups.ts 的 PITR 升级类型定义：
 * - BackupMethod 类型（'pitr' | 'pg_dump'）；
 * - BackupRecordItem 含 backup_method + backup_timestamp 字段；
 * - RestoreBackupBody 含 recovery_target_time 可选字段；
 *
 * 使用 TypeScript 类型断言 + vitest 运行时验证。
 */

import { describe, expect, it, vi } from 'vitest';
import type {
  BackupMethod,
  BackupRecordItem,
  RestoreBackupBody,
} from './backups';

// vi.mock 必须在 import 之前
vi.mock('./client', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

// ============================================================
// BackupMethod 类型测试
// ============================================================

describe('BackupMethod type', () => {
  it('accepts "pitr"', () => {
    const method: BackupMethod = 'pitr';
    expect(method).toBe('pitr');
  });

  it('accepts "pg_dump"', () => {
    const method: BackupMethod = 'pg_dump';
    expect(method).toBe('pg_dump');
  });
});

// ============================================================
// BackupRecordItem 新增字段测试
// ============================================================

describe('BackupRecordItem PITR fields', () => {
  it('includes backup_method field', () => {
    const item: BackupRecordItem = {
      id: 'test-id',
      job_id: null,
      backup_type: 'daily',
      name: null,
      description: null,
      backup_date: null,
      file_path: '/backups/test',
      file_size: null,
      sha256: null,
      status: 'succeeded',
      migration_version: null,
      application_version: null,
      created_by: null,
      created_at: '2026-08-16T02:00:00.000+00:00',
      completed_at: null,
      expires_at: null,
      error_message: null,
      backup_method: 'pitr',
      backup_timestamp: '2026-08-16T02:00:00.000+00:00',
    };

    expect(item.backup_method).toBe('pitr');
    expect(item.backup_timestamp).toBe('2026-08-16T02:00:00.000+00:00');
  });

  it('allows backup_method to be null', () => {
    const item: BackupRecordItem = {
      id: 'test-id',
      job_id: null,
      backup_type: 'daily',
      name: null,
      description: null,
      backup_date: null,
      file_path: '/backups/test',
      file_size: null,
      sha256: null,
      status: 'succeeded',
      migration_version: null,
      application_version: null,
      created_by: null,
      created_at: '2026-08-16T02:00:00.000+00:00',
      completed_at: null,
      expires_at: null,
      error_message: null,
      backup_method: null,
      backup_timestamp: null,
    };

    expect(item.backup_method).toBeNull();
    expect(item.backup_timestamp).toBeNull();
  });

  it('accepts pg_dump as backup_method', () => {
    const item: BackupRecordItem = {
      id: 'test-id',
      job_id: null,
      backup_type: 'milestone',
      name: 'old-backup',
      description: null,
      backup_date: null,
      file_path: '/backups/old',
      file_size: 1024,
      sha256: 'abc123',
      status: 'succeeded',
      migration_version: '0060',
      application_version: '0.0.9',
      created_by: null,
      created_at: '2026-07-01T00:00:00.000+00:00',
      completed_at: '2026-07-01T00:01:00.000+00:00',
      expires_at: null,
      error_message: null,
      backup_method: 'pg_dump',
      backup_timestamp: null,
    };

    expect(item.backup_method).toBe('pg_dump');
  });
});

// ============================================================
// RestoreBackupBody 新增 recovery_target_time 测试
// ============================================================

describe('RestoreBackupBody recovery_target_time', () => {
  it('accepts recovery_target_time', () => {
    const body: RestoreBackupBody = {
      recovery_target_time: '2026-08-16T10:30:00+00:00',
    };

    expect(body.recovery_target_time).toBe('2026-08-16T10:30:00+00:00');
  });

  it('allows empty body (recovery_target_time is optional)', () => {
    const body: RestoreBackupBody = {};

    expect(body.recovery_target_time).toBeUndefined();
  });

  it('accepts skip_migrations with recovery_target_time', () => {
    const body: RestoreBackupBody = {
      skip_migrations: true,
      recovery_target_time: '2026-08-16T10:30:00+00:00',
    };

    expect(body.skip_migrations).toBe(true);
    expect(body.recovery_target_time).toBe('2026-08-16T10:30:00+00:00');
  });
});

// ============================================================
// apiRestoreBackup 传递 recovery_target_time 测试
// ============================================================

describe('apiRestoreBackup passes recovery_target_time', () => {
  it('sends recovery_target_time in request body', async () => {
    const { http } = await import('./client');
    const { apiRestoreBackup } = await import('./backups');

    vi.mocked(http.post).mockResolvedValue({
      data: {
        job_id: 'job-1',
        backup_id: 'backup-1',
        status: 'accepted',
        kind: 'restore',
        created_at: '2026-08-16T02:00:00.000+00:00',
      },
    });

    await apiRestoreBackup('backup-1', {
      recovery_target_time: '2026-08-16T10:30:00+00:00',
    });

    expect(http.post).toHaveBeenCalledWith(
      '/backups/backup-1/restore',
      { recovery_target_time: '2026-08-16T10:30:00+00:00' },
    );
  });

  it('sends empty body when no recovery_target_time', async () => {
    const { http } = await import('./client');
    const { apiRestoreBackup } = await import('./backups');

    vi.mocked(http.post).mockResolvedValue({
      data: {
        job_id: 'job-2',
        backup_id: 'backup-2',
        status: 'accepted',
        kind: 'restore',
        created_at: '2026-08-16T02:00:00.000+00:00',
      },
    });

    await apiRestoreBackup('backup-2');

    expect(http.post).toHaveBeenCalledWith('/backups/backup-2/restore', {});
  });
});
