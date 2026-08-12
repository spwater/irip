import { describe, expect, it, vi, beforeEach } from 'vitest';

// Mock the http client — vi.mock is hoisted, so factory must be self-contained
vi.mock('./client', () => {
  const mockHttp = {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  };
  return { http: mockHttp };
});

import { http } from './client';
import {
  createTurn,
  createSynthesisTurn,
  createManualConclusion,
  listTimeline,
  reviseConclusion,
  requestFollowup,
} from './researchTimeline';

const mockHttp = http as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  patch: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

describe('researchTimeline API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('createTurn', () => {
    it('sends POST with correct body and idempotency key', async () => {
      mockHttp.post.mockResolvedValueOnce({
        data: {
          turn_id: 't1',
          workspace_id: 'ws1',
          turn_number: 1,
          kind: 'analysis',
          status: 'question_draft',
          question_text: '问题',
          question_origin: 'manual',
          evidence_snapshot_id: 's1',
        },
      });
      await createTurn('ws1', {
        question_text: '问题',
        evidence_snapshot_id: 's1',
      });
      expect(mockHttp.post).toHaveBeenCalled();
      const [url, body] = mockHttp.post.mock.calls[0];
      expect(url).toContain('/workspaces/ws1/turns');
      expect(body.question_text).toBe('问题');
      expect(body.idempotency_key).toBeTruthy();
      expect(body.idempotency_key.startsWith('web-')).toBe(true);
    });

    it('uses provided idempotency key if given', async () => {
      mockHttp.post.mockResolvedValueOnce({ data: {} });
      await createTurn('ws1', {
        question_text: '问题',
        evidence_snapshot_id: 's1',
        idempotency_key: 'custom-key',
      });
      const [, body] = mockHttp.post.mock.calls[0];
      expect(body.idempotency_key).toBe('custom-key');
    });
  });

  describe('createSynthesisTurn', () => {
    it('sends POST with 2+ revision IDs', async () => {
      mockHttp.post.mockResolvedValueOnce({ data: {} });
      await createSynthesisTurn('ws1', {
        evidence_snapshot_id: 's1',
        selected_conclusion_revision_ids: ['r1', 'r2'],
      });
      const [, body] = mockHttp.post.mock.calls[0];
      expect(body.selected_conclusion_revision_ids).toEqual(['r1', 'r2']);
    });
  });

  describe('createManualConclusion', () => {
    it('sends POST with statement', async () => {
      mockHttp.post.mockResolvedValueOnce({ data: {} });
      await createManualConclusion('ws1', {
        statement: '手工结论',
      });
      const [, body] = mockHttp.post.mock.calls[0];
      expect(body.statement).toBe('手工结论');
    });
  });

  describe('listTimeline', () => {
    it('sends GET with cursor and page_size params', async () => {
      mockHttp.get.mockResolvedValueOnce({
        data: { items: [], next_cursor: null, active_run_status: null },
      });
      await listTimeline('ws1', 'cursor-abc', 20);
      const [url, config] = mockHttp.get.mock.calls[0];
      expect(url).toContain('/workspaces/ws1/timeline');
      expect(config?.params?.cursor).toBe('cursor-abc');
      expect(config?.params?.page_size).toBe('20');
    });

    it('sends GET without params when not provided', async () => {
      mockHttp.get.mockResolvedValueOnce({
        data: { items: [], next_cursor: null, active_run_status: null },
      });
      await listTimeline('ws1');
      const [url, config] = mockHttp.get.mock.calls[0];
      expect(url).toContain('/workspaces/ws1/timeline');
      expect(config?.params?.cursor).toBeUndefined();
      expect(config?.params?.page_size).toBeUndefined();
    });
  });

  describe('reviseConclusion', () => {
    it('sends PATCH with expected_lock_version', async () => {
      mockHttp.patch.mockResolvedValueOnce({ data: {} });
      await reviseConclusion('ws1', 'c1', {
        statement: '修订',
        expected_lock_version: 0,
      });
      const [url, body] = mockHttp.patch.mock.calls[0];
      expect(url).toContain('/conclusions/c1');
      expect(body.expected_lock_version).toBe(0);
    });
  });

  describe('requestFollowup', () => {
    it('sends POST with snapshot_id and revisions', async () => {
      mockHttp.post.mockResolvedValueOnce({ data: {} });
      await requestFollowup('ws1', {
        snapshot_id: 's1',
        selected_conclusion_revision_ids: ['r1'],
      });
      const [, body] = mockHttp.post.mock.calls[0];
      expect(body.snapshot_id).toBe('s1');
      expect(body.selected_conclusion_revision_ids).toEqual(['r1']);
    });
  });
});
