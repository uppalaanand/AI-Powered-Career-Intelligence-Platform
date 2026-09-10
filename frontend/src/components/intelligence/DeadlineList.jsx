import { CalendarClock } from 'lucide-react';
import { Badge, EmptyState } from '../ui';
import { deadlineState, priorityMeta } from '../../utils/status';

/**
 * Deadlines are derived from action items rather than extracted separately, so
 * a date can never appear here without the task it belongs to.
 */
export function DeadlineList({ actionItems = [] }) {
  const withDeadlines = actionItems.filter((item) => item.deadline);

  if (!withDeadlines.length) {
    return (
      <EmptyState icon={CalendarClock} title="No deadlines mentioned">
        No dates or timeframes were stated for any of the tasks in this meeting.
      </EmptyState>
    );
  }

  return (
    <ul className="point-list">
      {withDeadlines.map((item, index) => {
        const overdue = deadlineState(item.deadline) === 'overdue';
        const priority = priorityMeta(item.priority);
        return (
          <li key={item.id || `${item.task}-${index}`}>
            <div className="row-between">
              <span>
                <strong style={overdue ? { color: 'var(--danger)' } : undefined}>
                  {item.deadline}
                </strong>{' '}
                &mdash; {item.task}
                {item.assigned_to && <span className="text-muted"> ({item.assigned_to})</span>}
              </span>
              <Badge tone={priority.tone}>{priority.label}</Badge>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
