import { Link } from 'react-router-dom';
import { Upload, Waves } from 'lucide-react';
import { Alert, Button, Card, EmptyState, Spinner, Stat } from '../components/ui';
import { MeetingsTable } from '../components/meetings/MeetingsTable';
import { useDashboard } from '../hooks/useMeetings';
import { formatDuration } from '../utils/format';

export function DashboardPage() {
  const { stats, loading, error } = useDashboard();

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
          <Link to="/upload">
            <Button variant="primary" icon={Upload}>
              Upload meeting
            </Button>
          </Link>
        </div>
      </div>

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
