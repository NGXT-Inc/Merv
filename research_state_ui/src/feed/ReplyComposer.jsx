import { useEffect, useRef, useState } from 'react';

// The product-wide hard cap on post text ("old Twitter, not an essay" —
// POST_TEXT_MAX in the backend feed service).
const TEXT_MAX = 280;

/**
 * Minimal inline reply composer under a post. Esc closes, Cmd/Ctrl+Enter
 * submits, the counter is live, and maxLength enforces the cap so the send
 * button never has an "over" state to explain.
 */
export default function ReplyComposer({ onSubmit, onClose }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const taRef = useRef(null);

  useEffect(() => { taRef.current?.focus(); }, []);

  const submit = () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    setError('');
    Promise.resolve(onSubmit(t))
      .then(() => onClose())
      .catch((e) => {
        setError(e?.message || 'Failed to post reply');
        setBusy(false);
      });
  };

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); onClose(); }
    else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
  };

  return (
    <div className="postcard-composer">
      <textarea
        ref={taRef}
        className="postcard-composer-input"
        value={text}
        maxLength={TEXT_MAX}
        rows={2}
        placeholder="Reply as Researcher…"
        aria-label="Reply text"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={busy}
      />
      <div className="postcard-composer-foot">
        {error && <span className="postcard-composer-err" role="alert">{error}</span>}
        <span className="postcard-composer-count" aria-hidden="true">{text.length}/{TEXT_MAX}</span>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn--sm postcard-composer-send"
          onClick={submit}
          disabled={!text.trim() || busy}
        >
          {busy ? 'Posting…' : 'Reply'}
        </button>
      </div>
    </div>
  );
}
