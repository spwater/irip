/**
 * V3 Governance + Audit + Jobs + Health API
 */
import { http } from './client';

// ============================================================
// Governance API
// ============================================================

export type UserListItem = { id: string; email: string; display_name: string; roles: string[]; status: string; department_id: string | null; created_at: string; updated_at: string; };
export type UserListResponse = { items: UserListItem[]; next_cursor: string | null; has_more: boolean; };

type UserListApiResponse = { items: Array<{ id: string; email: string; display_name: string; roles: string[]; status: string; department_id: string | null; created_at: string; updated_at: string; }>; next_cursor: string | null; has_more: boolean; };

export async function apiListUsers(params?: { status?: string; cursor?: string; limit?: number; }): Promise<UserListResponse> { const res = await http.get<UserListApiResponse>('/governance/users', { params }); return { items: res.data.items.map((u) => ({ id: u.id, email: u.email, display_name: u.display_name, roles: u.roles ?? [], status: u.status, department_id: u.department_id ?? null, created_at: u.created_at, updated_at: u.updated_at })), next_cursor: res.data.next_cursor, has_more: res.data.has_more }; }
export async function apiCreateUser(params: { email: string; display_name: string; password: string; roles: string[]; department_id?: string; }): Promise<UserListItem> { const res = await http.post<UserListItem>('/governance/users', params); return res.data; }
export async function apiUpdateUser(userId: string, params: { display_name?: string; password?: string; roles?: string[]; department_id?: string | null; }): Promise<UserListItem> { const res = await http.patch<UserListItem>(`/governance/users/${userId}`, params); return res.data; }
export async function apiAssignRoles(userId: string, roles: string[]): Promise<UserListItem> { const res = await http.post<UserListItem>(`/governance/users/${userId}/roles`, { roles }); return res.data; }
export async function apiRemoveRole(userId: string, role: string): Promise<UserListItem> { const res = await http.delete<UserListItem>(`/governance/users/${userId}/roles/${encodeURIComponent(role)}`); return res.data; }
export async function apiUpdateUserStatus(userId: string, status: 'active' | 'disabled'): Promise<UserListItem> { const res = await http.patch<UserListItem>(`/governance/users/${userId}/status`, { status }); return res.data; }
export async function apiDeleteUser(userId: string): Promise<void> { await http.delete(`/governance/users/${userId}`); }

// ============================================================
// Audit API
// ============================================================

export type AuditEventItem = { id: string; occurred_at: string; actor_user_id: string | null; department_id: string; action: string; resource_type: string | null; resource_id: string | null; payload: Record<string, unknown> | null; ip: string | null; user_agent: string | null; };
export type AuditEventListResponse = { items: AuditEventItem[]; next_cursor: string | null; has_more: boolean; };
export type AuditExportResponse = { job_id: string; status: string; kind: string; };

type AuditEventListApiResponse = { items: Array<{ id: string; occurred_at: string; actor_user_id: string | null; department_id: string; action: string; resource_type: string | null; resource_id: string | null; payload: Record<string, unknown> | null; ip: string | null; user_agent: string | null; }>; next_cursor: string | null; has_more: boolean; };

export async function apiListAuditEvents(params: { object_type?: string; object_id?: string; user_id?: string; action?: string; start_date?: string; end_date?: string; cursor?: string; limit?: number; }): Promise<AuditEventListResponse> { const res = await http.get<AuditEventListApiResponse>('/audit-events/', { params }); return { items: res.data.items.map((e) => ({ ...e })), next_cursor: res.data.next_cursor, has_more: res.data.has_more }; }
export async function apiCreateAuditExport(body: { object_type: string | null; object_id: string | null; user_id: string | null; action: string | null; start_date: string | null; end_date: string | null; format: string; }): Promise<AuditExportResponse> { const res = await http.post<AuditExportResponse>('/audit-events/export', body); return res.data; }

// ============================================================
// Jobs API
// ============================================================

export type JobListItem = { id: string; kind: string; status: string; stage: string; progress: number; retryable: boolean; created_at: string; attempt: number; max_attempts: number; flow_name: string; dept_name: string; };
export type JobListResponse = { items: JobListItem[]; next_cursor: string | null; has_more: boolean; };
export type JobDetail = { id: string; kind: string; status: string; stage: string; progress: number; retryable: boolean; attempt: number; max_attempts: number; created_at: string; updated_at: string; created_by: string | null; created_by_name: string | null; last_error: Record<string, unknown> | null; result: Record<string, unknown> | null; payload: Record<string, unknown> | null; };

type JobListApiResponse = { items: Array<{ id: string; kind: string; status: string; stage: string; progress: number; retryable: boolean; created_at: string; attempt: number; max_attempts: number; flow_name?: string; dept_name?: string; }>; next_cursor: string | null; has_more: boolean; };
type JobDetailApiResponse = { id: string; kind: string; status: string; stage: string; progress: number; retryable: boolean; attempt: number; max_attempts: number; created_at: string; updated_at: string; created_by: string | null; created_by_name: string | null; last_error: Record<string, unknown> | null; result: Record<string, unknown> | null; payload: Record<string, unknown> | null; };
type JobRetryApiResponse = { job_id: string; status: string; kind: string; };

export async function apiListJobs(params?: { status?: string; kind?: string; cursor?: string; limit?: number; }): Promise<JobListResponse> { const res = await http.get<JobListApiResponse>('/jobs', { params }); return { items: res.data.items.map((j) => ({ id: j.id, kind: j.kind, status: j.status, stage: j.stage ?? '', progress: j.progress ?? 0, retryable: j.retryable ?? false, created_at: j.created_at, attempt: j.attempt ?? 0, max_attempts: j.max_attempts ?? 3, flow_name: j.flow_name ?? '', dept_name: j.dept_name ?? '' })), next_cursor: res.data.next_cursor, has_more: res.data.has_more }; }
export async function apiGetJobDetail(id: string): Promise<JobDetail> { const res = await http.get<JobDetailApiResponse>(`/jobs/${id}/detail`); return { ...res.data, stage: res.data.stage ?? '', progress: res.data.progress ?? 0, retryable: res.data.retryable ?? false, attempt: res.data.attempt ?? 0, max_attempts: res.data.max_attempts ?? 3 }; }
export async function apiRetryJob(id: string): Promise<{ id: string; status: string; kind: string }> { const res = await http.post<JobRetryApiResponse>(`/jobs/${id}/retry`); return { id: res.data.job_id, status: res.data.status, kind: res.data.kind }; }

// ============================================================
// Data Transfer + Root Data Stats API (P1-T1-03/T1-05)
// ============================================================

export type DataTransferResponse = {
  table: string;
  from_dept_id: string;
  to_dept_id: string;
  dry_run: boolean;
  affected_rows: number;
};

export type RootDataStatsResponse = {
  root_department_id: string;
  root_department_name: string;
  stats: Array<{ table: string; display_name: string; count: number }>;
};

export async function apiDataTransfer(body: {
  table: string;
  from_dept_id: string;
  to_dept_id: string;
  dry_run: boolean;
}): Promise<DataTransferResponse> {
  const res = await http.post<DataTransferResponse>('/governance/data-transfer', body);
  return res.data;
}

export async function apiGetRootDataStats(): Promise<RootDataStatsResponse> {
  const res = await http.get<RootDataStatsResponse>('/governance/root-data-stats');
  return res.data;
}

// ============================================================
// Health API
// ============================================================

export type HealthCheck = { name: string; status: string; latency_ms: number | null; message: string | null; };
export type SystemHealth = { status: string; checks: HealthCheck[]; migration_version: string | null; worker_heartbeat: string | null; outbox_backlog: number; };

type HealthReadyApiResponse = { status: string; checks: Record<string, { status: string; version?: string; error?: string; [key: string]: unknown; }>; };

export async function apiGetSystemHealth(): Promise<SystemHealth> {
  let rawData: HealthReadyApiResponse;
  try { const res = await http.get<HealthReadyApiResponse>('/health/ready'); rawData = res.data; }
  catch (err) {
    if (err && typeof err === 'object' && 'response' in err) {
      const response = (err as { response?: { data?: HealthReadyApiResponse; status?: number } }).response;
      if (response?.data && response.status === 503) { rawData = response.data; } else { throw err; }
    } else { throw err; }
  }
  const checks: HealthCheck[] = Object.entries(rawData.checks).map(([name, detail]) => {
    let message: string | null = null;
    if (detail.error) { message = String(detail.error); } else if (detail.version) { message = `version: ${detail.version}`; }
    return { name, status: detail.status, latency_ms: null, message };
  });
  const dbCheck = rawData.checks['database'];
  const outboxCheck = rawData.checks['outbox'];
  const redisCheck = rawData.checks['redis'];
  return { status: rawData.status === 'ok' ? 'ok' : 'not_ready', checks, migration_version: dbCheck?.version ?? null, worker_heartbeat: redisCheck?.status === 'ok' ? new Date().toISOString() : null, outbox_backlog: typeof outboxCheck?.stale_undelivered === 'number' ? outboxCheck.stale_undelivered : 0 };
}
