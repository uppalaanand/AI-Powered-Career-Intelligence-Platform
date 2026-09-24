/**
 * Meeting knowledge endpoints (Milestone 3).
 *
 * Two different things, deliberately kept apart:
 *
 *   search() - semantic search. A local embedding + one vector search, no LLM,
 *              so it is fast and costs no Groq quota. Returns matching meetings.
 *   ask()    - RAG. Retrieves context, then makes ONE Groq request for a
 *              grounded answer plus its sources.
 *
 * index() is explicit on purpose: nothing here runs automatically on page load,
 * so simply opening the page never spends API quota.
 */
import { get, post, del } from './client';

export const knowledgeApi = {
  /** Semantic search across indexed meetings. No LLM call. */
  search(query, { topK, filters } = {}) {
    return post('/api/meetings/search', {
      query,
      ...(topK ? { top_k: topK } : {}),
      ...(filters ? { filters } : {}),
    });
  },

  /** Ask a grounded question. One LLM call through the existing fallback chain. */
  ask(question, { topK, filters } = {}) {
    return post('/api/meetings/ask', {
      question,
      ...(topK ? { top_k: topK } : {}),
      ...(filters ? { filters } : {}),
    });
  },

  /** Configuration and coverage. Reads local config + the database only. */
  status() {
    return get('/api/knowledge/status');
  },

  /** Build the index from meetings already in Supabase. Unchanged ones are skipped. */
  index({ limit, force = false } = {}) {
    return post('/api/knowledge/index', { ...(limit ? { limit } : {}), force });
  },

  /** Index or refresh a single meeting. */
  indexMeeting(meetingId, { force = false } = {}) {
    return post(`/api/knowledge/meetings/${meetingId}?force=${force ? 'true' : 'false'}`);
  },

  /** Remove one meeting's vectors. The meeting itself is untouched. */
  removeMeeting(meetingId) {
    return del(`/api/knowledge/meetings/${meetingId}`);
  },
};
