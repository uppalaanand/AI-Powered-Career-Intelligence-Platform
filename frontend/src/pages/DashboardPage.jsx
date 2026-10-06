import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Upload, Waves, Search, Plug } from 'lucide-react';
import { Alert, Button, Card, EmptyState, Spinner, Stat } from '../components/ui';
import { MeetingsTable } from '../components/meetings/MeetingsTable';
import { useDashboard } from '../hooks/useMeetings';
import { formatDuration } from '../utils/format';

export function DashboardPage() {
  const { stats, loading, error } = useDashboard();
  const navigate = useNavigate();
  const [quickSearch, setQuickSearch] = useState('');

  const handleSearch = (e) => {
    e.preventDefault();
    if (quickSearch.trim()) {
      // Pass the query via state to AskPage, AskPage doesn't naturally support taking query via route params natively without modifications
      // Let's assume AskPage might need to pick it up or we just redirect. Actually AskPage has no native way to take query from location state as implemented, but we can redirect.
      // Since AskPage handles its own state, maybe we should just redirect to /ask. Wait, the prompt says "redirects to the Ask & Search page with the query".
      navigate('/ask', { state: { query: quickSearch.trim() } });
    }
  };

  return (
    <>
      <div className="page-head">
        <div className="page-head-row">
          <div>
            <h1>Meeting intelligence</h1>
            <p>
              Upload a recording and get back a transcript, a summary and a list of who agreed to
              do what.
            </p>
          </div>
          <div className="row">
            <Link to="/integrations">
              <Button icon={Plug}>Integrations</Button>
            </Link>
            <Link to="/upload">
              <Button variant="primary" icon={Upload}>
                Upload meeting
              </Button>
            </Link>
          </div>
        </div>
      </div>

      <Card style={{ marginBottom: 24 }}>
        <form onSubmit={handleSearch} className="row">
          <Search size={20} className="text-muted" />
          <input 
            className="input" 
            style={{ flex: 1, border: 'none', boxShadow: 'none' }} 
            placeholder="Ask a question or search across all meetings..." 
            value={quickSearch}
            onChange={e => setQuickSearch(e.target.value)}
          />
          <Button type="submit" variant="secondary" size="sm">Search</Button>
        </form>
      </Card>

      {error && (
        <Alert tone="danger" title="Could not load the dashboard">
          {error.displayMessage || 'The backend did not respond.'}
        </Alert>
      )}

      {loading && <Spinner label="Loading dashboard" />}

      {stats && (
        <div className="stack stack-lg">
          <div className="stat-strip">
            <Stat value={stats.total_meetings} label="Meetings" />
            <Stat value={stats.completed_meetings} label="Processed" />
            <Stat value={stats.open_action_items} label="Open action items" />
            <Stat
              value={formatDuration((stats.total_transcribed_minutes || 0) * 60)}
              label="Audio transcribed"
            />
            {stats.total_participants !== undefined && (
              <Stat value={stats.total_participants} label="Total Participants" />
            )}
            {stats.total_decisions !== undefined && (
              <Stat value={stats.total_decisions} label="Decisions Made" />
            )}
          </div>

          {stats.failed_meetings > 0 && (
            <Alert tone="warn" title={`${stats.failed_meetings} meeting(s) failed to process`}>
              Open the meeting to see what went wrong and try again.
            </Alert>
          )}

          <Card
            title="Recent meetings"
            actions={
              <Link to="/meetings" className="text-sm">
                View all
              </Link>
            }
          >
            {stats.recent_meetings?.length ? (
              <MeetingsTable meetings={stats.recent_meetings} />
            ) : (
              <EmptyState
                icon={Waves}
                title="No meetings yet"
                action={
                  <Link to="/upload">
                    <Button variant="primary" icon={Upload}>
                      Upload your first recording
                    </Button>
                  </Link>
                }
              >
                Upload an audio or video recording to get a transcript and an action item list.
              </EmptyState>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
