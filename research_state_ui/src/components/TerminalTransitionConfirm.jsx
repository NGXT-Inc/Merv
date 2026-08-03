import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

const COPY = {
  complete: {
    eyebrow: 'Terminal state · Complete',
    title: 'Complete experiment?',
    status: 'Complete',
    trackingEffect: 'finish its tracking run',
    confirmLabel: 'Complete experiment',
  },
  mark_failed: {
    eyebrow: 'Terminal state · Failed',
    title: 'Mark experiment failed?',
    status: 'Failed',
    trackingEffect: 'mark its tracking run failed',
    confirmLabel: 'Mark failed',
  },
  abandon: {
    eyebrow: 'Terminal state · Abandoned',
    title: 'Abandon experiment?',
    status: 'Abandoned',
    trackingEffect: 'stop its tracking run',
    confirmLabel: 'Abandon experiment',
  },
};

/**
 * Confirmation boundary for experiment transitions that permanently close the
 * workflow. The ordinary forward-path actions remain one click.
 */
export default function TerminalTransitionConfirm({
  transition,
  experimentName,
  busy = false,
  error = null,
  onConfirm,
  onCancel,
}) {
  const cancelRef = useRef(null);
  const confirmRef = useRef(null);
  const busyRef = useRef(busy);
  const onCancelRef = useRef(onCancel);

  useEffect(() => {
    busyRef.current = busy;
    onCancelRef.current = onCancel;
  }, [busy, onCancel]);

  useEffect(() => {
    if (!transition) return undefined;
    const previouslyFocused = document.activeElement;
    cancelRef.current?.focus();

    function onKeyDown(event) {
      if (event.key === 'Escape' && !busyRef.current) onCancelRef.current();
      if (event.key === 'Tab') {
        const buttons = [cancelRef.current, confirmRef.current]
          .filter(button => button && !button.disabled);
        if (buttons.length === 0) {
          event.preventDefault();
        } else if (event.shiftKey && document.activeElement === buttons[0]) {
          event.preventDefault();
          buttons[buttons.length - 1].focus();
        } else if (!event.shiftKey && document.activeElement === buttons[buttons.length - 1]) {
          event.preventDefault();
          buttons[0].focus();
        }
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [transition]);

  const copy = COPY[transition];
  if (!copy) return null;

  const body = (
    <div
      className="retention-modal-overlay terminal-confirm-overlay"
      onMouseDown={() => { if (!busy) onCancel(); }}
    >
      <div
        className="retention-modal terminal-confirm"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="terminal-confirm-title"
        aria-describedby="terminal-confirm-description terminal-confirm-warning"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="terminal-confirm-eyebrow">{copy.eyebrow}</div>
        <h2 id="terminal-confirm-title" className="terminal-confirm-title">
          {copy.title}
        </h2>
        <p id="terminal-confirm-description" className="terminal-confirm-copy">
          This will set <strong>{experimentName || 'this experiment'}</strong> to{' '}
          <strong>{copy.status}</strong>, {copy.trackingEffect}, and close it to all
          further workflow transitions.
        </p>
        <p id="terminal-confirm-warning" className="terminal-confirm-warning">
          This action cannot be undone.
        </p>
        {error && <div className="terminal-confirm-error" role="alert">{error}</div>}
        <div className="terminal-confirm-actions">
          <button
            ref={cancelRef}
            type="button"
            className="btn"
            disabled={busy}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="btn btn--danger"
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? 'Applying…' : copy.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(body, document.body);
}
