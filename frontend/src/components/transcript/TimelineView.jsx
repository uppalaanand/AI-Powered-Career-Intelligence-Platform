import { Clock } from 'lucide-react';
import { EmptyState } from '../ui';
import { formatTimestamp } from '../../utils/format';

/**
 * Timeline view: every segment with its start and end timestamp, and the
 * speaker label when the transcription backend provides one.
 */
export function TimelineView({ segments = [] }) {
  if (!segments.length) {
    return (
      <EmptyState icon={Clock} title="No timed segments">
        This transcript has no segment timings, so the timeline cannot be shown. The paragraph
        view still has the full text.
      </EmptyState>
    );
  }

  return (
    <div className="timeline">
      {segments.map((segment) => {
        const duration = Math.max(0, (segment.end_time ?? 0) - (segment.start_time ?? 0));
        return (
          <article className="timeline-row" key={segment.segment_index ?? `${segment.start_time}`}>
            <div className="timeline-time">
              <time>{formatTimestamp(segment.start_time)}</time>
              <span aria-hidden="true"> - </span>
              <time>{formatTimestamp(segment.end_time)}</time>
              <span className="timeline-duration">{duration.toFixed(1)}s</span>
            </div>
            <div className="timeline-body">
              {segment.speaker && <span className="timeline-speaker">{segment.speaker}:</span>}
              {segment.text}
            </div>
          </article>
        );
      })}
    </div>
  );
}
