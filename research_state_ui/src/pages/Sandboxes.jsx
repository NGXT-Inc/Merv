import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useProjectStore, selectExperiments, selectEventsAll } from '../store/useProjectStore';
import { api } from '../api';
import SandboxTable from '../components/SandboxTable';
import ProviderConfig from '../components/ProviderConfig';

const STATUS_TABS = ['all', 'running', 'provisioning', 'terminated'];

/**
 * Sandboxes — the compute surface, two pages under one header.
 *
 * Active is the instance console: status, hardware, lifetime, and endpoint
 * per row, with an expand-to-terminal drawer (rows render in the shared
 * SandboxTable, also used on Home). Configure is the provider directory:
 * connect a cloud once, then the enable switch decides whether agents may
 * procure there. The page is deep-linkable: ?view=configure.
 */
export default function Sandboxes() {
  const projectId = useProjectStore(s => s.projectId);
  const experiments = useProjectStore(selectExperiments);
  const events = useProjectStore(selectEventsAll);
  const [sandboxes, setSandboxes] = useState(null);
  const [error, setError] = useState(null);
  const [filterStatus, setFilterStatus] = useState('all');
  const [params, setParams] = useSearchParams();
  const view = params.get('view') === 'configure' ? 'configure' : 'active';
  const setView = (next) => {
    setParams(next === 'configure' ? { view: 'configure' } : {}, { replace: true });
  };

  const fetchSandboxes = useCallback(async () => {
    try {
      const data = await api.listSandboxes(projectId);
      setSandboxes(data.sandboxes || []);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, [projectId]);

  useEffect(() => { fetchSandboxes(); }, [fetchSandboxes]);

  useEffect(() => {
    if (view !== 'active') return undefined;
    const anyActive = (sandboxes || []).some(
      s => s.status === 'running' || s.status === 'provisioning',
    );
    const t = setInterval(fetchSandboxes, anyActive ? 3000 : 10000);
    return () => clearInterval(t);
  }, [fetchSandboxes, sandboxes, view]);

  const counts = useMemo(() => {
    const map = { all: (sandboxes || []).length };
    for (const s of (sandboxes || [])) map[s.status] = (map[s.status] || 0) + 1;
    return map;
  }, [sandboxes]);

  const filtered = useMemo(() => {
    if (filterStatus === 'all') return sandboxes || [];
    return (sandboxes || []).filter(s => s.status === filterStatus);
  }, [sandboxes, filterStatus]);

  return (
    <div className="page-stage">
      <header className="page-header">
        <h1 className="page-title">Compute fleet</h1>
        <div className="sbx-view-row">
          {['active', 'configure'].map(v => (
            <button
              key={v}
              className={`sbx-view${view === v ? ' active' : ''}`}
              onClick={() => setView(v)}
            >
              {v === 'active' && (counts.running || 0) > 0 && <span className="sbxt-tab-dot" />}
              {v}
            </button>
          ))}
        </div>
        {view === 'active' && (
          <div className="tab-row" style={{ marginTop: 12 }}>
            {STATUS_TABS.map(s => (
              <button key={s} className={`tab${filterStatus === s ? ' active' : ''}`} onClick={() => setFilterStatus(s)}>
                {s === 'running' && (counts.running || 0) > 0 && <span className="sbxt-tab-dot" />}
                {s}
                <span className="tab-count">{counts[s] || 0}</span>
              </button>
            ))}
          </div>
        )}
      </header>

      {view === 'configure' ? (
        <ProviderConfig projectId={projectId} />
      ) : (
        <>
          {error && <div className="error-message">{error}</div>}
          {sandboxes == null ? (
            <div className="empty">Loading…</div>
          ) : (
            <SandboxTable
              sandboxes={filtered}
              experiments={experiments}
              events={events}
              projectId={projectId}
              empty={(
                <div className="empty-state">
                  <h2>No sandboxes</h2>
                  {sandboxes.length > 0 && <p>{`No ${filterStatus} sandboxes.`}</p>}
                </div>
              )}
            />
          )}
        </>
      )}
    </div>
  );
}
