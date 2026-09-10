/** Transcription accuracy (Word Error Rate) endpoint. */
import { post } from './client';

export const accuracyApi = {
  compare({
    referenceTranscript,
    meetingId,
    hypothesisTranscript,
    targetAccuracy = 90,
    ignoreCase = true,
    ignorePunctuation = true,
    ignoreFillerWords = true,
  }) {
    return post('/api/transcription/accuracy', {
      reference_transcript: referenceTranscript,
      meeting_id: meetingId,
      hypothesis_transcript: hypothesisTranscript,
      target_accuracy: targetAccuracy,
      ignore_case: ignoreCase,
      ignore_punctuation: ignorePunctuation,
      ignore_filler_words: ignoreFillerWords,
    });
  },
};
