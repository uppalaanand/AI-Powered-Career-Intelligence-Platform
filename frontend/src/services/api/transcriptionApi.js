/** Transcription endpoints: run Whisper, read and download transcripts. */
import { downloadFile, get, post } from './client';

export const transcriptionApi = {
  /** Runs FFmpeg + Whisper. Slow by nature, so it uses the long client timeout. */
  transcribe(meetingId, { force = false } = {}) {
    return post(`/api/meetings/${meetingId}/transcribe?force=${force}`);
  },

  getTranscript(meetingId) {
    return get(`/api/meetings/${meetingId}/transcript`);
  },

  /** @param {'txt'|'timeline'|'json'|'csv'} format */
  download(meetingId, format) {
    const extension = format === 'json' ? 'json' : format === 'csv' ? 'csv' : 'txt';
    return downloadFile(
      `/api/meetings/${meetingId}/transcript/download?format=${format}`,
      `transcript.${extension}`
    );
  },
};
