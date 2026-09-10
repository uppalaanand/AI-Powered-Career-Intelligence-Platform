/** Health and configuration endpoints. */
import { get } from './client';

export const systemApi = {
  health({ deep = false } = {}) {
    return get(`/api/health?deep=${deep}`, { timeoutMs: 15000 });
  },
  formats() {
    return get('/api/config/formats', { timeoutMs: 15000 });
  },
  checkDatabase() {
    return get('/api/health/database', { timeoutMs: 30000 });
  },
  checkLlm() {
    return get('/api/health/llm', { timeoutMs: 60000 });
  },
  checkFfmpeg() {
    return get('/api/health/ffmpeg', { timeoutMs: 15000 });
  },
};
