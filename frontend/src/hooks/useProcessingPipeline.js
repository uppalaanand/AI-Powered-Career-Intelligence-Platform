import { useCallback, useRef, useState } from 'react';
import { intelligenceApi, meetingApi, transcriptionApi } from '../services/api';
import { PIPELINE_STAGES, STAGE_STATE } from '../utils/constants';

/**
 * Drives the end-to-end pipeline and reports honest stage state.
 *
 * A stage only turns green once the backend call that performs it has actually
 * returned. Nothing is marked complete on a timer, and nothing is animated to
 * look like progress that has not happened.
 *
 * Upload and transcription are separate backend calls, so `upload` and
 * `validate` resolve together (validation happens inside the upload request),
 * while `audio`, `transcribe` and `transcript-check` all resolve when the
 * transcription call returns - that single call performs all three server-side.
 */
export function useProcessingPipeline() {
  const [stages, setStages] = useState(() => initialStages());
  const [uploadPercent, setUploadPercent] = useState(0);
  const [meeting, setMeeting] = useState(null);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(false);
  const cancelled = useRef(false);

  const setStage = useCallback((id, state, detail) => {
    setStages((current) =>
      current.map((stage) =>
        stage.id === id ? { ...stage, state, ...(detail ? { detail } : {}) } : stage
      )
    );
  }, []);

  const reset = useCallback(() => {
    cancelled.current = false;
    setStages(initialStages());
    setUploadPercent(0);
    setMeeting(null);
    setError(null);
    setRunning(false);
  }, []);

  /**
   * @param {File} file
   * @param {{title?: string, runAnalysis?: boolean}} options
   */
  const start = useCallback(
    async (file, { title, runAnalysis = true } = {}) => {
      reset();
      setRunning(true);
      cancelled.current = false;

      try {
        // ---- 1 & 2: upload, which validates server-side before it returns ---
        setStage('upload', STAGE_STATE.ACTIVE);
        const uploadResult = await meetingApi.upload(file, {
          title,
          onProgress: (percent) => setUploadPercent(percent),
        });
        if (cancelled.current) return null;

        const uploadedMeeting = uploadResult.meeting;
        setMeeting(uploadedMeeting);
        setStage('upload', STAGE_STATE.DONE);
        setStage('validate', STAGE_STATE.DONE, describeMedia(uploadedMeeting));

        // ---- 3, 4 & 5: FFmpeg, Whisper and transcript checks -------------
        setStage('audio', STAGE_STATE.ACTIVE);
        setStage('transcribe', STAGE_STATE.ACTIVE);
        const transcriptResult = await transcriptionApi.transcribe(uploadedMeeting.id);
        if (cancelled.current) return null;

        const transcript = transcriptResult.transcript;
        setStage('audio', STAGE_STATE.DONE);
        setStage(
          'transcribe',
          STAGE_STATE.DONE,
          `${transcript.word_count} words in ${transcript.segments?.length || 0} segments`
        );
        setStage('transcript-check', STAGE_STATE.DONE, 'Text and timestamps verified');

        // ---- 6: Grok analysis ---------------------------------------------
        if (!runAnalysis) {
          setStage('analyze', STAGE_STATE.SKIPPED, 'Skipped');
          setStage('save', STAGE_STATE.DONE, 'Transcript saved');
          setRunning(false);
          return { meeting: uploadedMeeting, transcript, intelligence: null };
        }

        setStage('analyze', STAGE_STATE.ACTIVE);
        const analysisResult = await intelligenceApi.analyze(uploadedMeeting.id);
        if (cancelled.current) return null;

        const intelligence = analysisResult.intelligence;
        setStage(
          'analyze',
          STAGE_STATE.DONE,
          `${intelligence.action_items?.length || 0} action items, ` +
            `${intelligence.participants?.length || 0} participants`
        );
        setStage('save', STAGE_STATE.DONE, 'Stored in Supabase');
        setRunning(false);
        return { meeting: uploadedMeeting, transcript, intelligence };
      } catch (caught) {
        if (cancelled.current) return null;
        setStages((current) => markFailure(current));
        setError(caught);
        setRunning(false);
        return null;
      }
    },
    [reset, setStage]
  );

  const cancel = useCallback(() => {
    cancelled.current = true;
    setRunning(false);
  }, []);

  return { stages, uploadPercent, meeting, error, running, start, reset, cancel };
}

function initialStages() {
  return PIPELINE_STAGES.map((stage) => ({ ...stage, state: STAGE_STATE.PENDING }));
}

/** The first stage still running is the one that failed. */
function markFailure(stages) {
  const activeIndex = stages.findIndex((stage) => stage.state === STAGE_STATE.ACTIVE);
  const failedIndex = activeIndex === -1 ? stages.findIndex((s) => s.state === STAGE_STATE.PENDING) : activeIndex;
  return stages.map((stage, index) =>
    index === failedIndex ? { ...stage, state: STAGE_STATE.FAILED } : stage
  );
}

function describeMedia(meeting) {
  const parts = [];
  if (meeting?.media_kind) parts.push(meeting.media_kind === 'video' ? 'Video' : 'Audio');
  if (meeting?.duration_seconds) parts.push(`${Math.round(meeting.duration_seconds)}s`);
  return parts.join(', ') || 'File accepted';
}
