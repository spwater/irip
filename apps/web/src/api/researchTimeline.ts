/**
 * Research Timeline API client — typed functions for the new timeline endpoints.
 *
 * Uses the project's shared axios `http` instance (with withCredentials)
 * so authentication cookies are automatically sent.
 */

import { http } from './client';

// ---- Types ----

export type TurnKind = "analysis" | "synthesis";
export type TurnStatus =
  | "question_draft"
  | "plan_draft"
  | "planning"
  | "plan_review"
  | "plan_confirmed"
  | "queued"
  | "running"
  | "succeeded"
  | "partially_succeeded"
  | "conclusion_reviewed"
  | "candidate_review"
  | "concluded"
  | "planning_failed"
  | "run_failed"
  | "succeeded_without_saved_conclusion"
  | "cancelled";

export type QuestionOrigin =
  | "initial_ai"
  | "followup_ai"
  | "ai_edited"
  | "manual"
  | "synthesis";

export type ExtractionStatus = "queued" | "running" | "succeeded" | "failed" | "task_lost";

export type CandidateStatus = "pending" | "saved" | "rejected";
export type SourceType = "ai_original" | "ai_edited" | "manual" | "assembled";
export type EvidenceStatus = "data_supported" | "manual_unverified";

export interface TimelineItem {
  turn_id: string;
  turn_number: number;
  kind: TurnKind;
  status: TurnStatus;
  question_text: string;
  question_origin: QuestionOrigin;
  snapshot_number: number;
  selected_conclusion_count: number;
  has_result: boolean;
  has_candidates: boolean;
  created_at: string;
}

export interface TimelinePage {
  items: TimelineItem[];
  next_cursor: string | null;
  active_run_status: TurnStatus | null;
}

export interface TurnRef {
  turn_id: string;
  workspace_id: string;
  turn_number: number;
  kind: TurnKind;
  status: TurnStatus;
  question_text: string;
  question_origin: QuestionOrigin;
  evidence_snapshot_id: string;
}

export interface BatchRef {
  batch_id: string;
  workspace_id: string;
  status: string;
  item_count: number;
}

export interface ConclusionRef {
  conclusion_id: string;
  workspace_id: string;
  source_type: SourceType;
  evidence_status: EvidenceStatus;
  status: string;
  revision_number: number;
  statement: string;
  current_revision_id?: string;
}

export interface RecommendationItem {
  id: string;
  position: number;
  question: string;
  rationale: string;
  evidence_hints: string[];
}

export interface RecommendationBatch {
  batch_id: string;
  status: string;
  items: RecommendationItem[];
}

export interface ConclusionCandidate {
  candidate_id: string;
  ordinal: number;
  statement: string;
  scope: string | null;
  confidence_level: string | null;
  limitations: string | null;
  status: CandidateStatus;
}

export interface PlanDetail {
  plan_id: string;
  version_number: number;
  status: "draft" | "confirmed" | "superseded";
  dag_structure: {
    steps: Array<{
      step_key: string;
      question: string;
      expected_output?: string;
      [key: string]: unknown;
    }>;
  };
  coverage_declaration: Record<string, unknown> | null;
}

export interface TurnDetail {
  turn: TurnRef;
  selected_conclusions: Array<{
    revision_id: string;
    statement: string;
    source_type: SourceType;
    evidence_status: EvidenceStatus;
  }>;
  plan: PlanDetail | null;
  result: Record<string, unknown> | null;
  fact_samples: Array<{ label: string; data: Record<string, unknown> }> | null;
  extraction_status: ExtractionStatus | null;
  candidates: ConclusionCandidate[];
  saved_conclusions: ConclusionRef[];
  access_restricted: boolean;
}

export type RunSSEEvent =
  | { event: "run.status_changed"; data: { run_id: string; status: string } }
  | { event: "candidate_extraction.status_changed"; data: { extraction_id: string; status: ExtractionStatus } }
  | { event: "conclusion_candidate.created"; data: { candidate_id: string; ordinal: number } };

// ---- API functions ----

// http instance already has baseURL=/api/v1, so we only need /research prefix
const BASE = "/research";

function genIdempotencyKey(): string {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function listTimeline(
  workspaceId: string,
  cursor?: string | null,
  pageSize?: number,
): Promise<TimelinePage> {
  const params: Record<string, string> = {};
  if (cursor) params.cursor = cursor;
  if (pageSize) params.page_size = String(pageSize);
  const res = await http.get<TimelinePage>(
    `${BASE}/workspaces/${workspaceId}/timeline`,
    { params },
  );
  return res.data;
}

export async function getTurnDetail(
  workspaceId: string,
  turnId: string,
): Promise<TurnDetail> {
  const res = await http.get<TurnDetail>(
    `${BASE}/workspaces/${workspaceId}/turns/${turnId}`,
  );
  return res.data;
}

export async function createTurn(
  workspaceId: string,
  body: {
    question_text: string;
    evidence_snapshot_id: string;
    selected_conclusion_revision_ids?: string[];
    recommendation_item_id?: string | null;
    idempotency_key?: string;
  },
): Promise<TurnRef> {
  const res = await http.post<TurnRef>(
    `${BASE}/workspaces/${workspaceId}/turns`,
    {
      ...body,
      idempotency_key: body.idempotency_key ?? genIdempotencyKey(),
    },
  );
  return res.data;
}

export async function createSynthesisTurn(
  workspaceId: string,
  body: {
    evidence_snapshot_id: string;
    selected_conclusion_revision_ids: string[];
    idempotency_key?: string;
  },
): Promise<TurnRef> {
  const res = await http.post<TurnRef>(
    `${BASE}/workspaces/${workspaceId}/synthesis-turns`,
    {
      ...body,
      idempotency_key: body.idempotency_key ?? genIdempotencyKey(),
    },
  );
  return res.data;
}

export async function requestFollowup(
  workspaceId: string,
  body: {
    snapshot_id: string;
    selected_conclusion_revision_ids?: string[];
    idempotency_key?: string;
  },
): Promise<BatchRef> {
  const res = await http.post<BatchRef>(
    `${BASE}/workspaces/${workspaceId}/recommendations/followup`,
    {
      ...body,
      idempotency_key: body.idempotency_key ?? genIdempotencyKey(),
    },
  );
  return res.data;
}

export async function getActiveRecommendation(
  workspaceId: string,
): Promise<RecommendationBatch> {
  const res = await http.get<RecommendationBatch>(
    `${BASE}/workspaces/${workspaceId}/recommendations/active`,
  );
  return res.data;
}

export async function retryRecommendation(
  workspaceId: string,
  batchId: string,
): Promise<BatchRef> {
  const res = await http.post<BatchRef>(
    `${BASE}/workspaces/${workspaceId}/recommendations/${batchId}/retry`,
  );
  return res.data;
}

export async function createManualConclusion(
  workspaceId: string,
  body: {
    statement: string;
    scope?: string | null;
    limitations?: string | null;
    idempotency_key?: string;
  },
): Promise<ConclusionRef> {
  const res = await http.post<ConclusionRef>(
    `${BASE}/workspaces/${workspaceId}/conclusions/manual`,
    {
      ...body,
      idempotency_key: body.idempotency_key ?? genIdempotencyKey(),
    },
  );
  return res.data;
}

export async function reviseConclusion(
  workspaceId: string,
  conclusionId: string,
  body: {
    statement: string;
    scope?: string | null;
    limitations?: string | null;
    expected_lock_version: number;
  },
): Promise<ConclusionRef> {
  const res = await http.patch<ConclusionRef>(
    `${BASE}/workspaces/${workspaceId}/conclusions/${conclusionId}`,
    body,
  );
  return res.data;
}

export async function startPlanning(
  workspaceId: string,
  turnId: string,
): Promise<{ turn_id: string; status: string }> {
  const res = await http.post<{ turn_id: string; status: string }>(
    `${BASE}/workspaces/${workspaceId}/turns/${turnId}/plan`,
  );
  return res.data;
}

export async function confirmPlan(
  workspaceId: string,
  turnId: string,
  planId: string,
): Promise<{ plan_id: string; turn_id: string; version_number: number; status: string }> {
  const res = await http.post<{
    plan_id: string;
    turn_id: string;
    version_number: number;
    status: string;
  }>(`${BASE}/workspaces/${workspaceId}/turns/${turnId}/confirm-plan`, {
    plan_id: planId,
  });
  return res.data;
}

export async function submitRun(
  workspaceId: string,
  turnId: string,
): Promise<{ run_id: string; turn_id: string; status: string }> {
  const res = await http.post<{ run_id: string; turn_id: string; status: string }>(
    `${BASE}/workspaces/${workspaceId}/turns/${turnId}/analyze`,
  );
  return res.data;
}
