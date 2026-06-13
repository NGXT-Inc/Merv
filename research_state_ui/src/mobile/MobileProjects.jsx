import { useNavigate } from 'react-router-dom';
import { useProjectStore } from '../store/useProjectStore';
import ObjId from '../components/ObjId';

/**
 * MobileProjects — read-only project switcher. The desktop page exposes
 * rename + create (both mutations, and "create" needs a server-local directory
 * path you can't type from a phone). Here you only switch. Create/rename live
 * on desktop. docs/MOBILE_UX_REVIEW.md §2.10.
 */
export default function MobileProjects() {
  const navigate = useNavigate();
  const projects = useProjectStore(s => s.projects);
  const projectId = useProjectStore(s => s.projectId);
  const setProjectId = useProjectStore(s => s.setProjectId);
  const refreshHome = useProjectStore(s => s.refreshHome);

  function switchTo(pid) {
    if (pid !== projectId) {
      setProjectId(pid);
      refreshHome();
    }
    navigate('/');
  }

  return (
    <div className="page-stage">
      <header className="page-header">
        <h1 className="page-title">Projects</h1>
        <p className="page-summary">Tap to switch. Create or rename a project from the desktop app or CLI.</p>
      </header>

      {projects.length === 0 ? (
        <div className="empty-state">
          <h2>No projects yet</h2>
          <p>Create one from the desktop app or the <span className="mono">research_plugin</span> CLI; it'll appear here.</p>
        </div>
      ) : (
        <div className="mcard-list">
          {projects.map(p => (
            <button
              key={p.id}
              type="button"
              className={`mcard${p.id === projectId ? ' mcard--attn' : ''}`}
              onClick={() => switchTo(p.id)}
            >
              <div className="mcard-head">
                <div className="mcard-title">{p.name || 'Untitled'}</div>
                {p.id === projectId && <span className="proj-active-tag">Active</span>}
              </div>
              {p.summary && <div className="mcard-sub">{p.summary}</div>}
              <div className="mcard-meta">
                <ObjId id={p.id} strong />
                {p.repo_root && <span className="mono">{p.repo_root}</span>}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
