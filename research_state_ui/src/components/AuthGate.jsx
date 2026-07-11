import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { api, request } from '../api';
import {
  getAuthToken,
  getSessionTokens,
  initAuth,
  onAuthChange,
  signInWithGoogle,
  signInWithPassword,
  signOut,
} from '../auth';

/**
 * AuthGate — mounts above the app. Fetches /api/meta once; when the backend
 * advertises auth.required (hosted control plane) it initializes the Supabase
 * session and holds the app behind a sign-in screen until a session exists.
 * Local mode: meta says required:false, the gate renders children untouched
 * and supabase-js is never loaded.
 */
export default function AuthGate({ children }) {
  const [state, setState] = useState({ checked: false, required: false, authed: false });

  useEffect(() => {
    let disposed = false;
    let unsubscribe = null;
    (async () => {
      // Meta is auth-exempt; a dead backend falls through to the app's own
      // boot-error surface rather than a misleading login wall.
      const meta = await api.getMeta().catch(() => null);
      const active = await initAuth(meta?.auth).catch(() => false);
      if (disposed) return;
      if (!active) {
        setState({ checked: true, required: false, authed: false });
        return;
      }
      const sync = () =>
        setState({ checked: true, required: true, authed: Boolean(getAuthToken()) });
      unsubscribe = onAuthChange(sync);
      sync();
    })();
    // A mid-session 401 (revoked account, rotated secret) clears the stale
    // session so the login screen returns instead of a silent data freeze.
    const onUnauthorized = () => {
      if (getAuthToken()) signOut();
    };
    window.addEventListener('rp:unauthorized', onUnauthorized);
    return () => {
      disposed = true;
      if (unsubscribe) unsubscribe();
      window.removeEventListener('rp:unauthorized', onUnauthorized);
    };
  }, []);

  const location = useLocation();
  if (!state.checked) return null;
  if (state.required && !state.authed) return <SignIn />;
  // CLI device-flow handoff: once signed in, /auth/sdk posts this browser's
  // session to the brain for the polling terminal — instead of the app,
  // which may still be booting (this route must work with zero projects).
  if (state.required && location.pathname === '/auth/sdk') return <SdkHandoff />;
  return children;
}

function SdkHandoff() {
  const [status, setStatus] = useState('working');
  useEffect(() => {
    const sessionId = new URLSearchParams(window.location.search).get('session');
    if (!sessionId) {
      setStatus('missing');
      return;
    }
    (async () => {
      try {
        const tokens = await getSessionTokens();
        if (!tokens) throw new Error('no session');
        await request('/api/sdk/auth/session/complete', {
          method: 'POST',
          body: { session_id: sessionId, ...tokens },
        });
        setStatus('done');
      } catch {
        setStatus('failed');
      }
    })();
  }, []);
  const message = {
    working: 'Completing sign-in…',
    done: 'Signed in — return to your terminal. You can close this tab.',
    missing: 'Missing login session. Rerun merv-client login and use the fresh link.',
    failed: 'Could not complete the sign-in. Rerun merv-client login and try again.',
  }[status];
  return (
    <div className="auth-gate">
      <div className="auth-gate-card">
        <h1 className="auth-gate-title">Merv</h1>
        <p className="auth-gate-hint">{message}</p>
      </div>
    </div>
  );
}

function SignIn() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await signInWithPassword(email.trim(), password);
    } catch (err) {
      setError(err.message || 'Sign-in failed');
    } finally {
      setBusy(false);
    }
  };

  const google = async () => {
    setError('');
    try {
      await signInWithGoogle();
    } catch (err) {
      setError(err.message || 'Sign-in failed');
    }
  };

  return (
    <div className="auth-gate">
      <form className="auth-gate-card" onSubmit={submit}>
        <h1 className="auth-gate-title">Merv</h1>
        <p className="auth-gate-hint">Sign in with your RapidReview account.</p>
        <input
          className="auth-gate-input"
          type="email"
          placeholder="Email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          className="auth-gate-input"
          type="password"
          placeholder="Password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <div className="auth-gate-error">{error}</div>}
        <button className="btn auth-gate-submit" type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <button className="btn btn--ghost auth-gate-google" type="button" onClick={google}>
          Continue with Google
        </button>
      </form>
    </div>
  );
}
