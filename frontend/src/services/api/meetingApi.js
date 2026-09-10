/** Meeting endpoints: upload, list, read, rename, delete. */
import { del, get, patch, uploadWithProgress } from './client';

export const meetingApi = {
  upload(file, { title, onProgress } = {}) {
    const formData = new FormData();
    formData.append('file', file);
    if (title) formData.append('title', title);
    return uploadWithProgress('/api/meetings/upload', formData, { onProgress });
  },

  list({ limit = 50, offset = 0, status } = {}) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) params.set('status', status);
    return get(`/api/meetings?${params.toString()}`);
  },

  dashboard() {
    return get('/api/meetings/dashboard');
  },

  getById(meetingId) {
    return get(`/api/meetings/${meetingId}`);
  },

  rename(meetingId, title) {
    return patch(`/api/meetings/${meetingId}?title=${encodeURIComponent(title)}`);
  },

  remove(meetingId) {
    return del(`/api/meetings/${meetingId}`);
  },
};
