import { Link } from 'react-router-dom';

/**
 * MobileProjectCreateNotice — replaces the desktop CreateProject form on the
 * phone (and at first-run bootstrap). Project creation needs a server-local
 * absolute directory path you cannot pick from a phone, so the desktop form
 * dead-ends here; say so honestly instead. docs/MOBILE_UX_REVIEW.md §2.10.
 */
export default function MobileProjectCreateNotice({ bootstrap = false }) {
  return (
    <div className="page-stage">
      <header className="page-header">
        <h1 className="page-title">{bootstrap ? 'No project yet' : 'New project'}</h1>
      </header>
      <div className="empty-state">
        <p>
          Creating a project needs a local directory on the machine running the
          daemon — a path you can't pick from a phone.
        </p>
        <p>
          Create it from the desktop app or the <span className="mono">research_plugin</span> CLI;
          it'll appear here automatically.
        </p>
        {!bootstrap && (
          <Link to="/projects" className="btn" style={{ marginTop: 12 }}>← Projects</Link>
        )}
      </div>
    </div>
  );
}
