import { BarChart, CheckCircle2, Clock, Users } from 'lucide-react';
import { Card, Stat, Badge, EmptyState } from '../ui';

export function AnalyticsPanel({ analytics }) {
  if (!analytics) {
    return (
      <Card>
        <EmptyState icon={BarChart} title="No analytics available">
          Run analysis to see meeting statistics.
        </EmptyState>
      </Card>
    );
  }

  const {
    participant_count = 0,
    action_items_count = 0,
    action_items_completed = 0,
    action_items_pending = 0,
    decision_count = 0,
    deadline_count = 0,
    transcript_word_count = 0,
    priority_distribution = {},
    action_items_by_participant = {}
  } = analytics;

  const totalPriority = Object.values(priority_distribution).reduce((a, b) => a + b, 0);

  return (
    <div className="stack stack-md">
      <div className="grid-4">
        <Card>
          <Stat value={participant_count} label="Participants" icon={Users} />
        </Card>
        <Card>
          <Stat value={transcript_word_count} label="Words spoken" />
        </Card>
        <Card>
          <Stat value={decision_count} label="Decisions made" />
        </Card>
        <Card>
          <Stat value={deadline_count} label="Deadlines set" icon={Clock} />
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Action Items Summary">
          <div className="stack stack-sm">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span>Total Items</span>
              <strong>{action_items_count}</strong>
            </div>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span className="row"><CheckCircle2 size={16} className="text-success" /> Completed</span>
              <strong>{action_items_completed}</strong>
            </div>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span className="row"><Clock size={16} className="text-warn" /> Pending</span>
              <strong>{action_items_pending}</strong>
            </div>
          </div>
        </Card>

        <Card title="Priority Distribution">
          {totalPriority > 0 ? (
            <div className="stack stack-sm">
              {Object.entries(priority_distribution).map(([priority, count]) => {
                const percentage = (count / totalPriority) * 100;
                let colorClass = 'bg-neutral';
                if (priority.toLowerCase() === 'high') colorClass = 'bg-danger';
                if (priority.toLowerCase() === 'medium') colorClass = 'bg-warn';
                if (priority.toLowerCase() === 'low') colorClass = 'bg-success';
                
                return (
                  <div key={priority} className="stack stack-xs">
                    <div className="row" style={{ justifyContent: 'space-between' }}>
                      <span className="text-sm capitalize">{priority}</span>
                      <span className="text-sm font-medium">{count}</span>
                    </div>
                    <div className="progress-bar-wrap" style={{ height: 8, background: '#f1f5f9', borderRadius: 4 }}>
                      <div 
                        className={`progress-bar-fill ${colorClass}`} 
                        style={{ width: `${percentage}%`, height: '100%', borderRadius: 4, background: priority.toLowerCase() === 'high' ? '#ef4444' : priority.toLowerCase() === 'medium' ? '#f59e0b' : priority.toLowerCase() === 'low' ? '#10b981' : '#94a3b8' }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-muted">No priorities assigned.</p>
          )}
        </Card>
      </div>

      <Card title="Action Items by Participant">
        {Object.keys(action_items_by_participant).length > 0 ? (
          <div className="stack stack-sm">
            {Object.entries(action_items_by_participant).map(([participant, count]) => (
              <div key={participant} className="row" style={{ justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid #e2e8f0' }}>
                <span className="text-sm font-medium">{participant}</span>
                <Badge tone="neutral">{count} {count === 1 ? 'item' : 'items'}</Badge>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">No action items assigned to specific participants.</p>
        )}
      </Card>
    </div>
  );
}
