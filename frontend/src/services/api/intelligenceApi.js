/**
 * Meeting intelligence endpoints.
 *
 * The backend analyses the transcript with Groq, its only LLM provider;
 * `intelligence.provider` and `intelligence.model` say what produced the result.
 * `analyze` without `force` returns an existing analysis untouched, so
 * re-opening a meeting never spends a Groq request.
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
