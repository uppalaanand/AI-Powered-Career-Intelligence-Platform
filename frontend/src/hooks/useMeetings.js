import { useCallback, useEffect, useState } from 'react';
import { meetingApi } from '../services/api';

/** Meeting list for the meetings screen. */
export function useMeetings({ status } = {}) {
  const [meetings, setMeetings] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await meetingApi.list({ status, limit: 100 });
      setMeetings(data?.meetings || []);
      setTotal(data?.total || 0);
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  return { meetings, total, loading, error, reload: load };
}

/** Dashboard statistics. */
export function useDashboard() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStats(await meetingApi.dashboard());
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return { stats, loading, error, reload: load };
}
