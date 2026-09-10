import { Users } from 'lucide-react';
import { EmptyState } from '../ui';
import { initials } from '../../utils/format';

/** Participants after normalisation and de-duplication. */
export function ParticipantList({ participants = [] }) {
  if (!participants.length) {
    return (
      <EmptyState icon={Users} title="No participants identified">
        No names were mentioned clearly enough in the transcript to identify who took part.
      </EmptyState>
    );
  }

  return (
    <div className="participant-grid">
      {participants.map((participant, index) => (
        <div className="participant" key={participant.id || `${participant.name}-${index}`}>
          <span className="avatar" aria-hidden="true">
            {initials(participant.name)}
          </span>
          <span>
            <span className="participant-name">{participant.name}</span>
            {participant.role && <span className="participant-meta"> &middot; {participant.role}</span>}
            {participant.aliases?.length > 0 && (
              <span className="participant-meta"> (also: {participant.aliases.join(', ')})</span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
