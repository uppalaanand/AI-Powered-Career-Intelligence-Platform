import { Badge } from '../ui';
import { isBusy, statusMeta } from '../../utils/status';

export function StatusBadge({ status }) {
  const meta = statusMeta(status);
  return (
    <Badge tone={meta.tone} dot={isBusy(status)}>
      {meta.label}
    </Badge>
  );
}
