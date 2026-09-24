/**
 * Ask & search - the Milestone 3 screen.
 *
 * One page, two tabs, built entirely from the existing UI kit (Card, Button,
 * Badge, Alert, EmptyState, Spinner, Tabs) so it looks like the rest of the app:
 *
 *   Ask a question -> grounded answer + the meetings it came from
 *   Search meetings -> the meetings that match, by meaning, with excerpts
 *
 * Nothing runs on load except a configuration check, which reads the backend's
 * own settings and makes no external call - so opening this page never spends
 * API quota.
 */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Database, MessageSquareText, Search, Sparkles, Upload } from 'lucide-react';
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  Spinner,
  TabPanel,
  Tabs,
} from '../components/ui';
import { knowledgeApi } from '../services/api/knowledgeApi';
import { useToast } from '../hooks/useToast';
import { formatDate } from '../utils/format';

const TABS = [
  { id: 'ask', label: 'Ask a question' },
  { id: 'search', label: 'Search meetings' },
];

const EXAMPLE_QUESTIONS = [
  'What deadline was decided for the mobile application?',
  'Which meeting discussed the database migration?',
  'What did we decide about the launch date?',
];

const SOURCE_LABELS = {
  transcript: 'Transcript',
  summary: 'Summary',
  decision: 'Decision',
  action_item: 'Action item',
  key_point: 'Key point',
  participants: 'Participants',
};

export function AskPage() {
  const toast = useToast();
  const [tab, setTab] = useState('ask');
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState(null);
  const [indexing, setIndexing] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await knowledgeApi.status());
      setStatusError(null);
    } catch (caught) {
      setStatusError(caught);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const runIndexing = async () => {
    setIndexing(true);
    try {
      const result = await knowledgeApi.index({});
      toast.success(
        `Indexed ${result.indexed} meeting(s). ${result.skipped_unchanged} were already up to date.`
      );
      await loadStatus();
    } catch (caught) {
      toast.error(caught.displayMessage || 'Indexing could not be completed.');
    } finally {
      setIndexing(false);
    }
  };

  const ready = status?.ready;

  return (
    <>
      <div className="page-head">
        <div className="page-head-row">
          <div>
            <h1>Ask &amp; search</h1>
            <p>
              Search every past meeting by meaning, or ask a question and get an answer grounded
              in your own meeting records.
            </p>
          </div>
          {ready && (
            <Button icon={Database} loading={indexing} onClick={runIndexing}>
              Index meetings
            </Button>
          )}
        </div>
      </div>

      {statusError && (
        <Alert tone="danger" title="Could not reach the backend">
          {statusError.displayMessage || 'The backend did not respond.'}
        </Alert>
      )}

      {status && !ready && <NotConfigured status={status} />}

      {ready && (
        <>
          <IndexCoverage status={status} indexing={indexing} onIndex={runIndexing} />

          <Card>
            <Tabs tabs={TABS} active={tab} onChange={setTab} ariaLabel="Ask or search" />
            <TabPanel id="ask" active={tab}>
              <AskPanel />
            </TabPanel>
            <TabPanel id="search" active={tab}>
              <SearchPanel />
            </TabPanel>
          </Card>
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ setup */

function NotConfigured({ status }) {
  return (
    <Alert tone="warn" title="Meeting search is not configured yet">
      <p>
        Semantic search and question answering need the local embedding model and a vector
        database. Everything else in the app works without them.
      </p>
      <ul>
        {!status.embeddings_configured && (
          <li>
            Install the backend requirements (<code>pip install -r requirements.txt</code>) so
            the local embedding model is available. It needs no API key.
          </li>
        )}
        {!status.vector_store_configured && (
          <li>
            Set <code>PINECONE_API_KEY</code> in <code>backend/.env</code>. Create a free index at{' '}
            <code>app.pinecone.io</code>.
          </li>
        )}
      </ul>
      <p>Restart the backend after editing the file, then reload this page.</p>
    </Alert>
  );
}

function IndexCoverage({ status, indexing, onIndex }) {
  const counts = status.counts || {};
  const indexed = counts.INDEXED || 0;
  const failed = counts.FAILED || 0;
  const stale = counts.STALE || 0;
  const nothingIndexed = indexed === 0;

  if (nothingIndexed) {
    return (
      <Card>
        <EmptyState
          icon={Database}
          title="No meetings indexed yet"
          action={
            <Button variant="primary" icon={Database} loading={indexing} onClick={onIndex}>
              Index my meetings
            </Button>
          }
        >
          Indexing reads the meetings already saved in your database and makes their
          transcripts, summaries, decisions and action items searchable. Meetings that have not
          changed are skipped, so it is cheap to run again.
        </EmptyState>
      </Card>
    );
  }

  return (
    <p className="text-xs text-muted">
      {indexed} meeting{indexed === 1 ? '' : 's'} indexed with {status.embedding_model} into{' '}
      <code>{status.index_name}</code>
      {failed > 0 && ` · ${failed} failed, try indexing again`}
      {stale > 0 && ` · ${stale} need re-indexing after an embedding model change`}
    </p>
  );
}

/* -------------------------------------------------------------------- ask */

function AskPanel() {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event?.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    setBusy(true);
    setError(null);
    try {
      setAnswer(await knowledgeApi.ask(trimmed));
    } catch (caught) {
      setError(caught);
      setAnswer(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack stack-md">
      <form className="stack stack-sm" onSubmit={submit}>
        <label className="text-xs text-muted" htmlFor="ask-input">
          Ask about your meetings
        </label>
        <div className="row">
          <input
            id="ask-input"
            className="input"
            style={{ flex: 1 }}
            placeholder="What deadline was decided for the mobile application?"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <Button type="submit" variant="primary" icon={Sparkles} loading={busy}>
            Ask
          </Button>
        </div>
      </form>

      {!answer && !busy && !error && (
        <div className="stack stack-sm">
          <span className="text-xs text-muted">Try one of these:</span>
          {EXAMPLE_QUESTIONS.map((example) => (
            <button
              key={example}
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setQuestion(example)}
            >
              {example}
            </button>
          ))}
        </div>
      )}

      {error && (
        <Alert tone="danger" title="Could not answer that">
          {error.displayMessage || 'Something went wrong. Try again in a moment.'}
        </Alert>
      )}

      {busy && <Spinner label="Searching your meetings and writing an answer" />}

      {answer && !busy && <AnswerCard answer={answer} />}
    </div>
  );
}

function AnswerCard({ answer }) {
  const found = answer.answer_found;

  return (
    <div className="stack stack-md">
      <Card
        title="Answer"
        actions={
          found ? (
            <Badge tone={answer.confidence === 'high' ? 'success' : 'neutral'}>
              {answer.confidence} confidence
            </Badge>
          ) : (
            <Badge tone="warn">Not found</Badge>
          )
        }
      >
        <p className="summary-text">{answer.answer}</p>
        {found && answer.provider && (
          <p className="text-xs text-muted">
            Answered by {answer.provider === 'groq' ? 'Groq' : answer.provider}
            {answer.model ? ` (${answer.model})` : ''} from {answer.searched_meetings} meeting
            {answer.searched_meetings === 1 ? '' : 's'} · {answer.took_ms} ms
          </p>
        )}
      </Card>

      {answer.sources?.length > 0 && (
        <Card title="Sources" hint="The meetings this answer was built from. Open one to check it.">
          <div className="stack stack-sm">
            {answer.sources.map((source) => (
              <SourceRow key={`${source.meeting_id}-${source.source_type}`} source={source} />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function SourceRow({ source }) {
  return (
    <div className="stack stack-sm">
      <div className="row">
        <Link to={`/meetings/${source.meeting_id}`}>{source.meeting_title}</Link>
        <Badge tone="neutral">{SOURCE_LABELS[source.source_type] || source.source_type}</Badge>
        {source.meeting_date && (
          <span className="text-xs text-muted">{formatDate(source.meeting_date)}</span>
        )}
      </div>
      {source.excerpt && <p className="text-xs text-muted">{source.excerpt}</p>}
    </div>
  );
}

/* ----------------------------------------------------------------- search */

function SearchPanel() {
  const [query, setQuery] = useState('');
  const [response, setResponse] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event?.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || busy) return;

    setBusy(true);
    setError(null);
    try {
      setResponse(await knowledgeApi.search(trimmed));
    } catch (caught) {
      setError(caught);
      setResponse(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack stack-md">
      <form className="stack stack-sm" onSubmit={submit}>
        <label className="text-xs text-muted" htmlFor="search-input">
          Search historical meetings
        </label>
        <div className="row">
          <input
            id="search-input"
            className="input"
            style={{ flex: 1 }}
            placeholder="Which meeting discussed the database migration?"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Button type="submit" variant="primary" icon={Search} loading={busy}>
            Search
          </Button>
        </div>
        <span className="text-xs text-muted">
          Searches by meaning, not keywords, so wording does not have to match.
        </span>
      </form>

      {error && (
        <Alert tone="danger" title="Search failed">
          {error.displayMessage || 'Unable to search meeting knowledge at the moment.'}
        </Alert>
      )}

      {busy && <Spinner label="Searching meetings" />}

      {response && !busy && (
        response.results.length ? (
          <div className="stack stack-sm">
            <span className="text-xs text-muted">
              {response.results.length} meeting{response.results.length === 1 ? '' : 's'} ·{' '}
              {response.total_matches} matching passage
              {response.total_matches === 1 ? '' : 's'} · {response.took_ms} ms
            </span>
            {response.results.map((result) => (
              <SearchResultRow key={result.meeting_id} result={result} />
            ))}
          </div>
        ) : (
          <EmptyState icon={MessageSquareText} title="No relevant meetings">
            {response.message || 'Nothing in your indexed meetings matched that search.'}
          </EmptyState>
        )
      )}
    </div>
  );
}

function SearchResultRow({ result }) {
  return (
    <Card
      title={result.meeting_title}
      actions={<Badge tone="neutral">{Math.round(result.score * 100)}% match</Badge>}
    >
      <div className="stack stack-sm">
        <div className="row">
          {result.meeting_date && (
            <span className="text-xs text-muted">{formatDate(result.meeting_date)}</span>
          )}
          {result.matched_source_types.map((type) => (
            <Badge key={type} tone="neutral">
              {SOURCE_LABELS[type] || type}
            </Badge>
          ))}
        </div>
        <p className="summary-text">{result.excerpt}</p>
        <Link to={`/meetings/${result.meeting_id}`}>Open meeting</Link>
      </div>
    </Card>
  );
}

/* Kept for the empty-database case: a link back to the existing upload flow. */
export function NoMeetingsHint() {
  return (
    <EmptyState
      icon={Upload}
      title="No meetings yet"
      action={
        <Link to="/upload">
          <Button variant="primary" icon={Upload}>
            Upload a recording
          </Button>
        </Link>
      }
    >
      Upload and analyse a meeting first, then it can be searched here.
    </EmptyState>
  );
}
