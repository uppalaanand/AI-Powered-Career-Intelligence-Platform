import { ACTION_STATUS_META, PRIORITY_META, STATUS, STATUS_META } from './constants';

export function statusMeta(status) {
  return STATUS_META[status] || { label: status || 'Unknown', tone: 'neutral' };
}

export function priorityMeta(priority) {
  return PRIORITY_META[priority] || PRIORITY_META.medium;
}

export function actionStatusMeta(status) {
  return ACTION_STATUS_META[status] || ACTION_STATUS_META.pending;
}

/** True while the backend is actively working on a meeting. */
export function isBusy(status) {
  return [
    STATUS.VALIDATING,
    STATUS.AUDIO_PROCESSING,
    STATUS.TRANSCRIBING,
    STATUS.TRANSCRIPT_VALIDATED,
    STATUS.AI_ANALYSIS,
    STATUS.PERSISTING,
  ].includes(status);
}

export function isFailed(status) {
  return status === STATUS.FAILED;
}

/**
 * A deadline is only "overdue" when it is a real date in the past. Deadlines are
 * stored exactly as spoken ("Friday", "next sprint"), so anything unparseable is
 * left alone rather than guessed at.
 */
export function deadlineState(deadline) {
  if (!deadline) return 'none';
  const parsed = Date.parse(deadline);
  if (Number.isNaN(parsed)) return 'relative';
  const now = new Date();
  const dueDate = new Date(parsed);
  if (dueDate < new Date(now.getFullYear(), now.getMonth(), now.getDate())) return 'overdue';
  return 'upcoming';
}
