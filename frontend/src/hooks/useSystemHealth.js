import { useCallback, useEffect, useState } from 'react';
import { systemApi } from '../services/api';

/**
 * Reads backend health once at startup so the UI can warn about missing
 * configuration before the user uploads a large file and hits a wall.
 */
export function useSystemHealth() {
  const [health, setHealth] = useState(null);
  const [formats, setFormats] = useState(null);
  const [offline, setOffline] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [healthData, formatData] = await Promise.all([
        systemApi.health(),
        systemApi.formats(),
      ]);
      setHealth(healthData);
      setFormats(formatData);
      setOffline(false);
    } catch {
      setOffline(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return { health, formats, offline, loading, reload: load };
}
