/**
 * Research Results API — publish conclusions and list/view results.
 *
 * Endpoints:
 *   POST  /research/workspaces/{ws}/conclusions/{cid}/publish  — publish
 *   GET   /research/workspaces/{ws}/results                      — list
 *   GET   /research/workspaces/{ws}/results/{rid}               — detail
 *
 * Reuses the shared axios `http` instance (withCredentials).
 */
import { http } from './client';

// ============================================================
// Types
// ============================================================

/** 发布结论请求体 */
export type PublishConclusionPayload = {
  title?: string;
  idempotency_key: string;
};

/** 发布结论响应 */
export type PublishConclusionResponse = {
  result_id: string;
  version_number: number;
};

/** 成果列表项 */
export type ResultItem = {
  id: string;
  name: string;
  status: string;
  current_version: number;
  created_at: string;
};

/** 成果列表响应 */
export type ResultListResponse = {
  items: ResultItem[];
};

/** 成果版本详情 */
export type ResultVersionDetail = {
  version_number: number;
  title: string;
  /** 解析后的结构化数据（metadata/points/series）或 null */
  summary: Record<string, unknown> | null;
  /** 来源结论 ID */
  source_conclusion_id: string;
  published_at: string;
  status: string;
};

/** 成果详情 */
export type ResultDetail = {
  id: string;
  name: string;
  status: string;
  current_version: number;
  created_at: string;
  version: ResultVersionDetail | null;
};

// ============================================================
// Helpers
// ============================================================

/** 生成幂等键 */
export function genResultIdempotencyKey(): string {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

// ============================================================
// API functions
// ============================================================

const BASE = '/research';

/** 发布一个结论为研究成果 */
export async function apiPublishConclusion(
  workspaceId: string,
  conclusionId: string,
  body: PublishConclusionPayload,
): Promise<PublishConclusionResponse> {
  const res = await http.post<PublishConclusionResponse>(
    `${BASE}/workspaces/${workspaceId}/conclusions/${conclusionId}/publish`,
    body,
  );
  return res.data;
}

/** 列出工作空间下所有成果 */
export async function apiListResults(
  workspaceId: string,
): Promise<ResultItem[]> {
  const res = await http.get<ResultListResponse>(
    `${BASE}/workspaces/${workspaceId}/results`,
  );
  return res.data.items ?? [];
}

/** 获取单个成果详情（含最新版本结构化数据） */
export async function apiGetResultDetail(
  workspaceId: string,
  resultId: string,
): Promise<ResultDetail> {
  const res = await http.get<ResultDetail>(
    `${BASE}/workspaces/${workspaceId}/results/${resultId}`,
  );
  return res.data;
}
