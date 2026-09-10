import { Link } from 'react-router-dom';
import { Upload, Waves } from 'lucide-react';
import { Alert, Button, Card, EmptyState, Spinner } from '../components/ui';
import { MeetingsTable } from '../components/meetings/MeetingsTable';
import { useMeetings } from '../hooks/useMeetings';

export function MeetingsPage() {
  const { meetings, total, loading, error } = useMeetings();

  return (
    <>
      <div className="page-head">
        <div className="page-head-row">
          <div>
            <h1>Meetings</h1>
            <p>Every recording you have uploaded, newest first.</p>
          </div>
          <Link to="/upload">
            <Button variant="primary" icon={Upload}>
              Upload meeting
            </Button>
          </Link>
        </div>
      </div>

      {error && (
        <Alert tone="danger" title="Could not load meetings">
          {error.displayMessage || 'The backend did not respond.'}
        </Alert>
      )}

      {loading && <Spinner label="Loading meetings" />}

      {!loading && !error && (
        <Card title={`${total} meeting${total === 1 ? '' : 's'}`}>
          {meetings.length ? (
            <MeetingsTable meetings={meetings} />
          ) : (
            <EmptyState
              icon={Waves}
              title="Nothing here yet"
              action={
                <Link to="/upload">
                  <Button variant="primary" icon={Upload}>
                    Upload a recording
                  </Button>
                </Link>
              }
            >
              Uploaded meetings and their transcripts will appear here.
            </EmptyState>
          )}
        </Card>
      )}
    </>
  );
}
