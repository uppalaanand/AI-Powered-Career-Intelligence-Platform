/**
 * Meeting intelligence endpoints.
 *
 * The backend picks the AI provider itself, trying Grok, then Gemini, then Groq
 * and stopping at the first valid answer; `intelligence.provider` says which one
 * responded. `analyze` without `force` returns an existing analysis untouched,
 * so re-opening a meeting never spends an API call.
 */
import { get, post } from './client';

export const intelligenceApi = {
  analyze(meetingId, { force = false } = {}) {
    return post(`/api/meetings/${meetingId}/analyze`, { force });
  },

  get(meetingId) {
    return get(`/api/meetings/${meetingId}/intelligence`);
  },
};
