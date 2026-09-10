import { Link } from 'react-router-dom';
import { FileAudio, FileVideo } from 'lucide-react';
import { StatusBadge } from './StatusBadge';
import { formatDuration, formatRelative } from '../../utils/format';

/** Meeting list. Kept as a real table so it stays readable and sortable-looking. */
export function MeetingsTable({ meetings }) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="visually-hidden">Uploaded meetings and their processing status</caption>
        <thead>
          <tr>
            <th scope="col">Meeting</th>
            <th scope="col">Length</th>
            <th scope="col">Status</th>
            <th scope="col">Results</th>
            <th scope="col">Uploaded</th>
          </tr>
        </thead>
        <tbody>
          {meetings.map((meeting) => {
            const Icon = meeting.media_kind === 'video' ? FileVideo : FileAudio;
            return (
              <tr key={meeting.id}>
                <td>
                  <div className="row" style={{ gap: 9, flexWrap: 'nowrap' }}>
                    <Icon size={16} aria-hidden="true" style={{ color: 'var(--ink-faint)', flexShrink: 0 }} />
                    <div style={{ minWidth: 0 }}>
                      <Link to={`/meetings/${meeting.id}`} className="cell-primary">
                        {meeting.title}
                      </Link>
                      <div className="text-xs text-muted">{meeting.original_filename}</div>
                    </div>
                  </div>
                </td>
                <td className="cell-muted mono">{formatDuration(meeting.duration_seconds)}</td>
                <td>
                  <StatusBadge status={meeting.status} />
                  {meeting.error_message && (
                    <div className="text-xs" style={{ color: 'var(--danger)', marginTop: 4 }}>
                      {meeting.error_message}
                    </div>
                  )}
                </td>
                <td className="cell-muted text-sm">
                  {[
                    meeting.has_transcript ? 'Transcript' : null,
                    meeting.has_intelligence ? 'Analysis' : null,
                  ]
                    .filter(Boolean)
                    .join(' + ') || <span className="cell-null">None yet</span>}
                </td>
                <td className="cell-muted text-sm">{formatRelative(meeting.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
