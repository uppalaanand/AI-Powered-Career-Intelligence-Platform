import { describe, expect, it } from 'vitest';
import {
  formatBytes,
  formatDuration,
  formatTimestamp,
  initials,
  toParagraphs,
} from '../utils/format';
import { deadlineState, isBusy, priorityMeta, statusMeta } from '../utils/status';
import { STATUS } from '../utils/constants';

describe('formatting helpers', () => {
  it('formats file sizes', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
  });

  it('formats timestamps, adding hours only when needed', () => {
    expect(formatTimestamp(0)).toBe('00:00');
    expect(formatTimestamp(73)).toBe('01:13');
    expect(formatTimestamp(3675)).toBe('01:01:15');
  });

  it('formats human durations', () => {
    expect(formatDuration(0)).toBe('-');
    expect(formatDuration(45)).toBe('45s');
    expect(formatDuration(200)).toBe('3m 20s');
    expect(formatDuration(3660)).toBe('1h 01m');
  });

  it('builds initials from names', () => {
    expect(initials('Ravi Kumar')).toBe('RK');
    expect(initials('Priya')).toBe('P');
    expect(initials('')).toBe('?');
  });

  it('splits transcript text into paragraphs', () => {
    expect(toParagraphs('One block.\n\nSecond block.')).toEqual(['One block.', 'Second block.']);
    expect(toParagraphs('')).toEqual([]);
  });
});

describe('status helpers', () => {
  it('labels every backend status', () => {
    Object.values(STATUS).forEach((status) => {
      expect(statusMeta(status).label).toBeTruthy();
    });
  });

  it('knows which states mean work is in progress', () => {
    expect(isBusy(STATUS.TRANSCRIBING)).toBe(true);
    expect(isBusy(STATUS.AI_ANALYSIS)).toBe(true);
    expect(isBusy(STATUS.COMPLETED)).toBe(false);
    expect(isBusy(STATUS.FAILED)).toBe(false);
  });

  it('maps priorities to display tones', () => {
    expect(priorityMeta('high').label).toBe('High');
    expect(priorityMeta('nonsense').label).toBe('Medium');
  });

  it('only marks real past dates as overdue', () => {
    expect(deadlineState(null)).toBe('none');
    expect(deadlineState('Friday')).toBe('relative');
    expect(deadlineState('2020-01-01')).toBe('overdue');
    expect(deadlineState('2999-01-01')).toBe('upcoming');
  });
});
