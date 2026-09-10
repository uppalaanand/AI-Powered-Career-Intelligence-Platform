import { AlertTriangle, ListTodo } from 'lucide-react';
import { Badge, EmptyState } from '../ui';
import { actionStatusMeta, deadlineState, priorityMeta } from '../../utils/status';

/**
 * Action items.
 *
 * Missing values are shown as "Not stated" rather than filled in with a guess:
 * if the transcript never named an owner or a date, that absence is the honest
 * answer and is displayed as such.
 */
export function ActionItemsTable({ items = [] }) {
  if (!items.length) {
    return (
      <EmptyState icon={ListTodo} title="No action items">
        Nobody committed to a specific task in this meeting, so there is nothing to track.
      </EmptyState>
    );
  }

  return (
    <div className="table-wrap">
      <table>
        <caption className="visually-hidden">Action items extracted from the meeting</caption>
        <thead>
          <tr>
            <th scope="col">Task</th>
            <th scope="col">Assigned to</th>
            <th scope="col">Deadline</th>
            <th scope="col">Priority</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => {
            const priority = priorityMeta(item.priority);
            const status = actionStatusMeta(item.status);
            const dueState = deadlineState(item.deadline);
            return (
              <tr key={item.id || `${item.task}-${index}`}>
                <td>
                  <div className="cell-primary">{item.task}</div>
                  {item.context && <div className="text-xs text-muted">{item.context}</div>}
                </td>
                <td>
                  {item.assigned_to ? (
                    item.assigned_to
                  ) : (
                    <span className="cell-null">Not stated</span>
                  )}
                </td>
                <td>
                  {item.deadline ? (
                    <span className="row" style={{ gap: 5, flexWrap: 'nowrap' }}>
                      {dueState === 'overdue' && (
                        <AlertTriangle size={13} style={{ color: 'var(--danger)' }} aria-hidden="true" />
                      )}
                      <span style={dueState === 'overdue' ? { color: 'var(--danger)' } : undefined}>
                        {item.deadline}
                      </span>
                    </span>
                  ) : (
                    <span className="cell-null">Not stated</span>
                  )}
                </td>
                <td>
                  <Badge tone={priority.tone}>{priority.label}</Badge>
                </td>
                <td>
                  <Badge tone={status.tone}>{status.label}</Badge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
