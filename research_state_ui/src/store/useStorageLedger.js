import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

/**
 * Self-contained loader for the heavy-file storage ledger.
 *
 * Deliberately NOT part of the project store: storage is an architecturally
 * separate feature, so its page owns its own fetch and degrades gracefully when
 * the backend storage API isn't present yet (a 404 → `unsupported`, not an error
 * banner). Both the desktop and mobile surfaces share this one hook.
 */
export function useStorageLedger(projectId, { kind = 'all', includeExpired = false } = {}) {
  const [objects, setObjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [unsupported, setUnsupported] = useState(false);

  const reload = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.listStorage(projectId, { kind, includeExpired });
      setObjects(data?.objects || []);
      setUnsupported(false);
    } catch (err) {
      // Backend storage API not wired yet → show the explanatory empty state.
      if (err.status === 404) { setUnsupported(true); setObjects([]); }
      else setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [projectId, kind, includeExpired]);

  useEffect(() => { reload(); }, [reload]);

  return { objects, loading, error, unsupported, reload };
}
