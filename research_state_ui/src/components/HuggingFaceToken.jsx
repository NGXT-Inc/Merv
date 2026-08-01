import { useState } from 'react';
import { api } from '../api';

/**
 * Personal Hugging Face token.
 *
 * The value is write-only by design: Merv stores it, uses it internally when
 * provisioning a sandbox, and has no route that reads it back. So this panel
 * can report what the last action did but can never show — or confirm the
 * presence of — a stored token. The field only ever holds what is being typed.
 *
 * It is a per-account secret, not a per-project one: setting it here applies to
 * every project you belong to.
 */
export default function HuggingFaceToken({ hosted }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [failed, setFailed] = useState(false);

  async function save() {
    setBusy(true);
    setFailed(false);
    setStatus('');
    try {
      await api.setHfToken(token.trim());
      setToken('');
      setStatus('Saved. Sandboxes for every project you belong to will use it.');
    } catch (err) {
      setFailed(true);
      setStatus(err?.message || 'Could not save the token.');
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true);
    setFailed(false);
    setStatus('');
    try {
      await api.clearHfToken();
      setToken('');
      setStatus('Cleared. Gated downloads will fail until you set one again.');
    } catch (err) {
      setFailed(true);
      setStatus(err?.message || 'Could not clear the token.');
    } finally {
      setBusy(false);
    }
  }

  if (!hosted) {
    return (
      <div className="empty-state empty-state--compact">
        <p>
          The Hugging Face token belongs to your hosted RapidReview account.
          Sign in on the hosted app to set or clear it.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="settings-panel-head">
        <p className="settings-summary">
          Bring your own token so sandboxes can pull gated models and datasets.
          It applies to every project you belong to — there is no
          deployment-wide Hugging Face secret.
        </p>
      </div>

      <div className="settings-field">
        <label className="settings-field-label" htmlFor="hf-token">
          Token
        </label>
        <div className="settings-field-row">
          <input
            id="hf-token"
            className="auth-input mono"
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="hf_…"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
          />
          <button
            type="button"
            className="btn btn--primary"
            onClick={save}
            disabled={busy || !token.trim()}
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={clear}
            disabled={busy}
          >
            Clear
          </button>
        </div>
        <p className="settings-field-note">
          Merv never displays a stored token, so this field cannot show whether
          one is already set. Saving replaces whatever is there.
        </p>
        {status && (
          <p
            className={`settings-field-status${failed ? ' settings-field-status--error' : ''}`}
            role="status"
          >
            {status}
          </p>
        )}
      </div>
    </>
  );
}
