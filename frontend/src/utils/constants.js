/** Processing states. These strings match the backend ProcessingStatus enum exactly. */
export const STATUS = {
  UPLOADED: 'UPLOADED',
  VALIDATING: 'VALIDATING',
  AUDIO_PROCESSING: 'AUDIO_PROCESSING',
  TRANSCRIBING: 'TRANSCRIBING',
  TRANSCRIPT_VALIDATED: 'TRANSCRIPT_VALIDATED',
  AI_ANALYSIS: 'AI_ANALYSIS',
  PERSISTING: 'PERSISTING',
  COMPLETED: 'COMPLETED',
  FAILED: 'FAILED',
};

/** How each backend state is described to a person, and how it is coloured. */
export const STATUS_META = {
  UPLOADED: { label: 'Uploaded', tone: 'neutral' },
  VALIDATING: { label: 'Validating', tone: 'info' },
  AUDIO_PROCESSING: { label: 'Processing audio', tone: 'info' },
  TRANSCRIBING: { label: 'Transcribing', tone: 'info' },
  TRANSCRIPT_VALIDATED: { label: 'Transcript checked', tone: 'info' },
  AI_ANALYSIS: { label: 'Analysing', tone: 'info' },
  PERSISTING: { label: 'Saving', tone: 'info' },
  COMPLETED: { label: 'Completed', tone: 'success' },
  FAILED: { label: 'Failed', tone: 'danger' },
};

/** Client-side pipeline stages shown on the processing screen. */
export const PIPELINE_STAGES = [
  { id: 'upload', name: 'Upload recording', detail: 'Sending the file to the server' },
  { id: 'validate', name: 'Validate file', detail: 'Format, size and a readable audio track' },
  { id: 'audio', name: 'Prepare audio', detail: 'FFmpeg extracts 16 kHz mono audio' },
  { id: 'transcribe', name: 'Transcribe speech', detail: 'Whisper converts speech to text' },
  { id: 'transcript-check', name: 'Check transcript', detail: 'Confirm the text and timestamps are usable' },
  { id: 'analyze', name: 'Analyse with AI', detail: 'Summary, decisions and action items' },
  { id: 'save', name: 'Save results', detail: 'Store everything in Supabase' },
];

export const STAGE_STATE = {
  PENDING: 'pending',
  ACTIVE: 'active',
  DONE: 'done',
  FAILED: 'failed',
  SKIPPED: 'skipped',
};

export const PRIORITY_META = {
  high: { label: 'High', tone: 'danger' },
  medium: { label: 'Medium', tone: 'warn' },
  low: { label: 'Low', tone: 'neutral' },
};

export const ACTION_STATUS_META = {
  pending: { label: 'Pending', tone: 'neutral' },
  in_progress: { label: 'In progress', tone: 'info' },
  completed: { label: 'Completed', tone: 'success' },
  blocked: { label: 'Blocked', tone: 'danger' },
};

export const DOWNLOAD_FORMATS = [
  { id: 'txt', label: 'Paragraph text', hint: '.txt' },
  { id: 'timeline', label: 'Timeline text', hint: '.txt' },
  { id: 'json', label: 'Structured data', hint: '.json' },
  { id: 'csv', label: 'Spreadsheet', hint: '.csv' },
];

export const DEFAULT_TARGET_ACCURACY = 90;
