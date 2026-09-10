import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Play, RotateCcw } from 'lucide-react';
import { Alert, Button, Card, ProgressBar } from '../components/ui';
import { DropZone, SelectedFile } from '../components/upload/DropZone';
import { ProcessingStages } from '../components/processing/ProcessingStages';
import { useProcessingPipeline } from '../hooks/useProcessingPipeline';
import { useSystemHealth } from '../hooks/useSystemHealth';
import { useToast } from '../hooks/useToast';

/**
 * Upload screen and processing screen in one flow: pick a file, watch the real
 * pipeline stages, then jump to the results.
 */
export function UploadPage() {
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState('');
  const [runAnalysis, setRunAnalysis] = useState(true);
  const [localError, setLocalError] = useState(null);
  const [finished, setFinished] = useState(false);

  const { formats, health, offline } = useSystemHealth();
  const pipeline = useProcessingPipeline();
  const navigate = useNavigate();
  const toast = useToast();

  const extensions = [...(formats?.audio || []), ...(formats?.video || [])];
  const maxSizeMb = formats?.max_upload_size_mb || 200;
  const started = pipeline.running || pipeline.meeting || pipeline.error;

  const start = async () => {
    if (!file) return;
    setFinished(false);
    const result = await pipeline.start(file, { title: title.trim() || undefined, runAnalysis });
    if (result) {
      setFinished(true);
      toast.success('Meeting processed.');
    }
  };

  const startOver = () => {
    pipeline.reset();
    setFile(null);
    setTitle('');
    setLocalError(null);
    setFinished(false);
  };

  return (
    <>
      <div className="page-head">
        <h1>Upload a meeting</h1>
        <p>
          Drop in an audio or video recording. It is transcribed with Whisper, then analysed for
          decisions and action items.
        </p>
      </div>

      <div className="stack stack-md">
        {offline && (
          <Alert tone="danger" title="The backend is not reachable">
            Start the API with <code>uvicorn app.main:app --reload</code> in the backend folder,
            then reload this page.
          </Alert>
        )}

        {health && !health.ffmpeg_available && (
          <Alert tone="danger" title="FFmpeg is missing on the server">
            Recordings cannot be processed until FFmpeg is installed. Check{' '}
            <code>ffmpeg -version</code> on the machine running the backend.
          </Alert>
        )}

        {health && !health.supabase_configured && (
          <Alert tone="warn" title="Supabase is not configured">
            Uploads will fail until <code>SUPABASE_URL</code> and{' '}
            <code>SUPABASE_SERVICE_ROLE_KEY</code> are set in <code>backend/.env</code> and{' '}
            <code>backend/database/schema.sql</code> has been run.
          </Alert>
        )}

        {health && !(health.llm_configured ?? health.grok_configured) && (
          <Alert tone="warn" title="No AI provider is configured">
            Transcription still works, but the summary and action items need at least one of{' '}
            <code>XAI_API_KEY</code>, <code>GEMINI_API_KEY</code> or <code>GROQ_API_KEY</code> in{' '}
            <code>backend/.env</code>. They are tried in that order, and the first one that
            answers is used.
          </Alert>
        )}

        {!started && (
          <Card>
            <div className="stack stack-md">
              <DropZone
                accept={formats?.accept_attribute}
                extensions={extensions}
                maxSizeMb={maxSizeMb}
                onSelect={(selected) => {
                  setFile(selected);
                  setLocalError(null);
                }}
                onReject={(message) => setLocalError(message)}
              />

              {localError && (
                <Alert tone="danger" title="That file cannot be used">
                  {localError}
                </Alert>
              )}

              <SelectedFile file={file} onClear={() => setFile(null)} />

              <div className="field" style={{ marginBottom: 0 }}>
                <label className="label" htmlFor="meeting-title">
                  Meeting title <span className="text-muted">(optional)</span>
                </label>
                <input
                  id="meeting-title"
                  className="input"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Defaults to the filename"
                  maxLength={200}
                />
              </div>

              <label className="row" style={{ gap: 8 }}>
                <input
                  type="checkbox"
                  checked={runAnalysis}
                  onChange={(event) => setRunAnalysis(event.target.checked)}
                />
                <span className="text-sm">
                  Analyse with AI after transcribing (summary, decisions, action items)
                </span>
              </label>

              <div className="row-between">
                <span className="text-xs text-muted">
                  {extensions.length
                    ? `Supported: ${extensions.join(', ')}`
                    : 'Loading supported formats...'}
                </span>
                <Button variant="primary" icon={Play} onClick={start} disabled={!file}>
                  Start processing
                </Button>
              </div>
            </div>
          </Card>
        )}

        {started && (
          <Card
            title="Processing"
            hint={
              pipeline.running
                ? 'This can take a few minutes. Keep this tab open.'
                : pipeline.error
                  ? 'Processing stopped.'
                  : 'All stages complete.'
            }
          >
            <div className="stack stack-md">
              {pipeline.running && pipeline.uploadPercent < 100 && (
                <div>
                  <div className="row-between text-sm" style={{ marginBottom: 6 }}>
                    <span>Uploading</span>
                    <span className="mono">{pipeline.uploadPercent}%</span>
                  </div>
                  <ProgressBar value={pipeline.uploadPercent} label="Upload progress" />
                </div>
              )}

              {pipeline.running && pipeline.uploadPercent >= 100 && (
                <ProgressBar indeterminate label="Processing on the server" />
              )}

              <ProcessingStages stages={pipeline.stages} />

              {pipeline.error && (
                <Alert tone="danger" title="Processing failed">
                  {pipeline.error.displayMessage || pipeline.error.message}
                </Alert>
              )}

              <div className="btn-row">
                {finished && pipeline.meeting && (
                  <Button
                    variant="primary"
                    icon={ArrowRight}
                    onClick={() => navigate(`/meetings/${pipeline.meeting.id}`)}
                  >
                    View results
                  </Button>
                )}
                {pipeline.error && pipeline.meeting && (
                  <Button onClick={() => navigate(`/meetings/${pipeline.meeting.id}`)}>
                    Open the meeting anyway
                  </Button>
                )}
                {!pipeline.running && (
                  <Button icon={RotateCcw} onClick={startOver}>
                    Upload another
                  </Button>
                )}
              </div>
            </div>
          </Card>
        )}
      </div>
    </>
  );
}
