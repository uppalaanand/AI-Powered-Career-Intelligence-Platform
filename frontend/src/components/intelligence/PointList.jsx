import { EmptyState } from '../ui';

/** Key points and decisions share one shape, so they share one component. */
export function PointList({ items = [], variant = 'point', emptyIcon, emptyTitle, emptyBody }) {
  if (!items.length) {
    return (
      <EmptyState icon={emptyIcon} title={emptyTitle}>
        {emptyBody}
      </EmptyState>
    );
  }

  return (
    <ul className={`point-list ${variant === 'decision' ? 'decision-list' : ''}`.trim()}>
      {items.map((item, index) => (
        <li key={item.id || `${item.text}-${index}`}>
          {item.text}
          {item.context && <div className="text-xs text-muted">{item.context}</div>}
        </li>
      ))}
    </ul>
  );
}
