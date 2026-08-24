import { describe, expect, it } from 'vitest';
import { mergeDisplayMessages, extractFactLabels } from './displayMessages';
import type { AssistantMessage } from '@/api/models-ai';

function makeMsg(id: string, role: string, content: string): AssistantMessage {
  return {
    id,
    conversation_id: 'conv1',
    role,
    content,
    tool_calls: [],
    citations: [],
    uncertainty: null,
    created_at: '2024-01-01T00:00:00Z',
    mentions: [],
    sender_user_id: null,
    sender_display_name: null,
    sender_avatar_url: null,
  };
}

describe('mergeDisplayMessages', () => {
  it('returns localMessages when db is empty and no streaming', () => {
    const local = [makeMsg('l1', 'user', 'hi')];
    const result = mergeDisplayMessages([], local, null, 'conv1');
    expect(result).toBe(local);
  });

  it('returns dbMessages when no local messages and no streaming', () => {
    const db = [makeMsg('d1', 'assistant', 'hello')];
    const result = mergeDisplayMessages(db, [], null, 'conv1');
    expect(result).toBe(db);
  });

  it('appends streaming AI message during streaming', () => {
    const db = [makeMsg('d1', 'user', 'old question')];
    const local = [makeMsg('l1', 'user', 'new question')];
    const result = mergeDisplayMessages(db, local, 'streaming answer...', 'conv1');
    expect(result).toHaveLength(3);
    expect(result[0]).toEqual(db[0]); // db history not in local
    expect(result[1]).toEqual(local[0]); // local user message
    expect(result[2].content).toBe('streaming answer...');
    expect(result[2].role).toBe('assistant');
    expect(result[2].id).toBe('streaming-ai');
  });

  it('deduplicates db messages that are also in local during streaming', () => {
    const sharedMsg = makeMsg('shared', 'user', 'same message');
    const db = [sharedMsg];
    const local = [sharedMsg];
    const result = mergeDisplayMessages(db, local, 'streaming', 'conv1');
    // shared should appear once (from local), plus streaming AI
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe('shared');
    expect(result[1].id).toBe('streaming-ai');
  });

  it('uses selectedConvId for streaming message', () => {
    const result = mergeDisplayMessages([], [], 'ans', 'my-conv');
    expect(result[0].conversation_id).toBe('my-conv');
  });

  it('falls back to empty string conv id when selectedConvId is null', () => {
    const result = mergeDisplayMessages([], [], 'ans', null);
    expect(result[0].conversation_id).toBe('');
  });
});

describe('extractFactLabels', () => {
  it('extracts single sample label', () => {
    const ctx = '### 样品: 样品A\nsome data';
    expect(extractFactLabels(ctx)).toEqual(['样品A']);
  });

  it('extracts multiple sample labels', () => {
    const ctx = '### 样品: 样品A\n### 样品: 样品B\n### 样品: 样品C';
    expect(extractFactLabels(ctx)).toEqual(['样品A', '样品B', '样品C']);
  });

  it('returns empty array when no match', () => {
    expect(extractFactLabels('no labels here')).toEqual([]);
  });

  it('returns empty array for empty string', () => {
    expect(extractFactLabels('')).toEqual([]);
  });
});
