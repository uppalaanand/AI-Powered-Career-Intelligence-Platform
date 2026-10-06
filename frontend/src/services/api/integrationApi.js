import { get, post } from './client';

export const integrationApi = {
  zoomAuth() {
    return post('/api/integrations/zoom/auth');
  },
  zoomRecordings() {
    return get('/api/integrations/zoom/recordings');
  },
  zoomImport(recordingId) {
    return post(`/api/integrations/zoom/recordings/${recordingId}/import`);
  },
  googleAuth() {
    return post('/api/integrations/google-meet/auth');
  },
  googleRecordings() {
    return get('/api/integrations/google-meet/recordings');
  },
  googleImport(recordingId) {
    return post(`/api/integrations/google-meet/recordings/${recordingId}/import`);
  }
};
