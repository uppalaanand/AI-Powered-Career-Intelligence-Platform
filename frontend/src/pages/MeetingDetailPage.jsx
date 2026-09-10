import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BrainCircuit,
  CheckSquare,
  Gavel,
  Lightbulb,
  Mic,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import { Alert, Badge, Button, Card, EmptyState, Spinner, TabPanel, Tabs } from '../components/ui';
import { StatusBadge } from '../components/meetings/StatusBadge';
import { ParagraphView } from '../components/transcript/ParagraphView';
import { TimelineView } from '../components/transcript/TimelineView';
import { DownloadMenu } from '../components/transcript/DownloadMenu';
import { AccuracyPanel } from '../components/transcript/AccuracyPanel';
import { ActionItemsTable } from '../components/intelligence/ActionItemsTable';
import { ParticipantList } from '../components/intelligence/ParticipantList';
import { PointList } from '../components/intelligence/PointList';
import { DeadlineList } from '../components/intelligence/DeadlineList';
import { intelligenceApi, meetingApi, transcriptionApi } from '../services/api';
import { useToast } from '../hooks/useToast';
import { formatBytes, formatDate, formatDuration } from '../utils/format';

export function MeetingDetailPage() {
  const { meetingId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();

  const [meeting, setMeeting] = useState(null);
  const [transcript, setTranscript] = useState(null);
  const [intelligence, setIntelligence] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [tab, setTab] = useState('transcript');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const meetingData = await meetingApi.getById(meetingId);
      setMeeting(meetingData);

      // Transcript and intelligence may legitimately not exist yet, so a 404
      // on either is a state to render, not an error to show.
      if (meetingData.has_transcript) {
        try {
          setTranscript(await transcriptionApi.getTranscript(meetingId));
        } catch {
          setTranscript(null);
        }
      }
      if (meetingData.has_intelligence) {
        try {
          const data = await intelligenceApi.get(meetingId);
          setIntelligence(data.intelligence);
        } catch {
          setIntelligence(null);
        }
      }
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }, [meetingId]);

  useEffect(() => {
    load();
  }, [load]);

  const runTranscription = async (force = false) => {
    setBusy('transcribe');
    try {
      const result = await transcriptionApi.transcribe(meetingId, { force });
      setTranscript(result.transcript);
      toast.success('Transcript ready.');
      await load();
    } catch (caught) {
      toast.error(caught.displayMessage || 'Transcription failed.');
    } finally {
      setBusy(null);
    }
  };

  const runAnalysis = async (force = false) => {
    setBusy('analyze');
    try {
      const result = await intelligenceApi.analyze(meetingId, { force });
      setIntelligence(result.intelligence);
      setTab('intelligence');
      toast.success('Analysis complete.');
      await load();
    } catch (caught) {
      toast.error(caught.displayMessage || 'Analysis failed.');
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!window.confirm('Delete this meeting, its transcript and its analysis?')) return;
    setBusy('delete');
    try {
      await meetingApi.remove(meetingId);
      toast.success('Meeting deleted.');
      navigate('/meetings');
    } catch (caught) {
      toast.error(caught.displayMessage || 'The meeting could not be deleted.');
      setBusy(null);
    }
  };

  if (loading) return <Spinner label="Loading meeting" />;

  if (error) {
    return (
      <>
        <Button icon={ArrowLeft} onClick={() => navigate('/meetings')}>
          Back to meetings
        </Button>
        <div style={{ marginTop: 16 }}>
          <Alert tone="danger" title="Could not open this meeting">
            {error.displayMessage || 'The backend did not respond.'}
          </Alert>
        </div>
      </>
    );
  }

  const tabs = [
    { id: 'transcript', label: 'Transcript' },
    { id: 'intelligence', label: 'Meeting intelligence' },
    { id: 'details', label: 'File details' },
  ];

  return (
    <>
      <Button icon={ArrowLeft} size="sm" onClick={() => navigate('/meetings')}>
        Back to meetings
      </Button>

      <div className="page-head" style={{ marginTop: 16 }}>
        <div className="page-head-row">
          <div>
            <h1>{meeting.title}</h1>
            <div className="row" style={{ marginTop: 8 }}>
              <StatusBadge status={meeting.status} />
              <span className="text-sm text-muted">
                {meeting.media_kind === 'video' ? 'Video' : 'Audio'} &middot;{' '}
                {formatDuration(meeting.duration_seconds)} &middot; {formatDate(meeting.created_at)}
              </span>
            </div>
          </div>

          <div className="btn-row">
            {!meeting.has_transcript ? (
              <Button
                variant="primary"
                icon={Mic}
                loading={busy === 'transcribe'}
                onClick={() => runTranscription(false)}
              >
                Transcribe
              </Button>
            ) : (
              <Button
                icon={RefreshCw}
                loading={busy === 'transcribe'}
                onClick={() => runTranscription(true)}
              >
                Re-transcribe
              </Button>
            )}

            {meeting.has_transcript && (
              <Button
                variant={meeting.has_intelligence ? 'secondary' : 'primary'}
                icon={BrainCircuit}
                loading={busy === 'analyze'}
                onClick={() => runAnalysis(meeting.has_intelligence)}
              >
                {meeting.has_intelligence ? 'Re-analyse' : 'Analyse with AI'}
              </Button>
            )}

            <Button variant="danger" icon={Trash2} loading={busy === 'delete'} onClick={remove}>
              Delete
            </Button>
          </div>
        </div>
      </div>

      {meeting.error_message && (
        <div style={{ marginBottom: 16 }}>
          <Alert tone="danger" title="The last run failed">
            {meeting.error_message}
          </Alert>
        </div>
      )}

      <Tabs tabs={tabs} active={tab} onChange={setTab} ariaLabel="Meeting views" />

      <TabPanel id="transcript" active={tab}>
        <TranscriptSection
          meeting={meeting}
          transcript={transcript}
          busy={busy === 'transcribe'}
          onTranscribe={() => runTranscription(false)}
        />
      </TabPanel>

      <TabPanel id="intelligence" active={tab}>
        <IntelligenceSection
          meeting={meeting}
          intelligence={intelligence}
          busy={busy === 'analyze'}
          onAnalyze={() => runAnalysis(false)}
        />
      </TabPanel>

      <TabPanel id="details" active={tab}>
        <FileDetails meeting={meeting} transcript={transcript} />
      </TabPanel>
    </>
  );
}

function TranscriptSection({ meeting, transcript, busy, onTranscribe }) {
  const [view, setView] = useState('paragraph');

  if (!transcript) {
    return (
      <Card>
        <EmptyState
          icon={Mic}
          title="No transcript yet"
          action={
            <Button variant="primary" icon={Mic} loading={busy} onClick={onTranscribe}>
              Transcribe this recording
            </Button>
          }
        >
          FFmpeg will extract the audio and Whisper will convert the speech to text.
        </EmptyState>
      </Card>
    );
  }

  const views = [
    { id: 'paragraph', label: 'Paragraph' },
    { id: 'timeline', label: 'Timeline', count: transcript.segments?.length || 0 },
  ];

  return (
    <div className="stack stack-md">
      <Card
        title="Transcript"
        hint={`${transcript.word_count} words${
          transcript.language ? ` \u00b7 detected language: ${transcript.language}` : ''
        }${transcript.model ? ` \u00b7 Whisper ${transcript.model}` : ''}`}
        actions={<DownloadMenu meetingId={meeting.id} />}
      >
        <Tabs tabs={views} active={view} onChange={setView} ariaLabel="Transcript views" />
        <TabPanel id="paragraph" active={view}>
          <ParagraphView transcript={transcript} />
        </TabPanel>
        <TabPanel id="timeline" active={view}>
          <TimelineView segments={transcript.segments} />
        </TabPanel>
      </Card>

      <AccuracyPanel meetingId={meeting.id} />
    </div>
  );
}

/** Display names for the AI providers the backend can fall back through. */
const PROVIDER_LABELS = { grok: 'Grok', gemini: 'Gemini', groq: 'Groq' };

/**
 * "Gemini (gemini-2.0-flash)" - which provider actually answered.
 *
 * `provider` is absent on results analysed before the fallback chain existed,
 * so the model name alone is still a valid answer.
 */
function describeProvider({ provider, model }) {
  const name = PROVIDER_LABELS[provider] || provider;
  if (name && model) return `${name} (${model})`;
  return name || model || 'AI';
}

function IntelligenceSection({ meeting, intelligence, busy, onAnalyze }) {
  if (!meeting.has_transcript) {
    return (
      <Card>
        <EmptyState icon={BrainCircuit} title="Transcribe the meeting first">
          The analysis reads the transcript, so a recording has to be transcribed before it can be
          analysed.
        </EmptyState>
      </Card>
    );
  }

  if (!intelligence) {
    return (
      <Card>
        <EmptyState
          icon={BrainCircuit}
          title="Not analysed yet"
          action={
            <Button variant="primary" icon={BrainCircuit} loading={busy} onClick={onAnalyze}>
              Analyse with AI
            </Button>
          }
        >
          The AI reads the transcript and pulls out a summary, the decisions, and who agreed to do
          what by when.
        </EmptyState>
      </Card>
    );
  }

  return (
    <div className="stack stack-md">
      <Card
        title="Summary"
        actions={
          intelligence.chunk_count > 1 ? (
            <Badge tone="neutral">Merged from {intelligence.chunk_count} sections</Badge>
          ) : null
        }
      >
        <p className="summary-text">{intelligence.summary}</p>
      </Card>

      <Card title="Action items" hint="Anything left blank was never stated in the meeting.">
        <ActionItemsTable items={intelligence.action_items} />
      </Card>

      <div className="grid-2">
        <Card title="Key points">
          <PointList
            items={intelligence.key_points}
            emptyIcon={Lightbulb}
            emptyTitle="No key points"
            emptyBody="Nothing substantive enough to record was discussed."
          />
        </Card>

        <Card title="Decisions">
          <PointList
            items={intelligence.decisions}
            variant="decision"
            emptyIcon={Gavel}
            emptyTitle="No decisions"
            emptyBody="The group did not settle on anything in this meeting."
          />
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Participants">
          <ParticipantList participants={intelligence.participants} />
        </Card>

        <Card title="Deadlines">
          <DeadlineList actionItems={intelligence.action_items} />
        </Card>
      </div>

      <p className="text-xs text-muted">
        Generated with {describeProvider(intelligence)}. Always check anything important against
        the transcript.
      </p>
    </div>
  );
}

function FileDetails({ meeting, transcript }) {
  const rows = [
    ['Original filename', meeting.original_filename],
    ['Format', meeting.file_extension ? `.${meeting.file_extension}` : '-'],
    ['Detected type', meeting.mime_type || '-'],
    ['Size', formatBytes(meeting.file_size_bytes)],
    ['Duration', formatDuration(meeting.duration_seconds)],
    ['Segments stored', meeting.segment_count ?? 0],
    ['Transcript words', meeting.transcript_word_count ?? '-'],
    ['Whisper model', meeting.transcript_model || '-'],
    ['Detected language', transcript?.language || meeting.transcript_language || '-'],
    ['Uploaded', formatDate(meeting.created_at)],
    ['Meeting id', meeting.id],
  ];

  return (
    <Card title="File details" hint="What the server found inside this recording.">
      <div className="table-wrap">
        <table>
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}>
                <th scope="row" style={{ width: 190 }}>
                  {label}
                </th>
                <td className={value === '-' ? 'cell-null' : ''}>{value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted" style={{ marginTop: 12 }}>
        The recording itself is deleted from the server once transcription finishes. Only the
        transcript and the analysis are kept.
      </p>
    </Card>
  );
}
