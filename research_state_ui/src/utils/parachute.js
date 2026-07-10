// Legacy parachute-event projection for historical databases. The current
// runtime performs no automatic rescue or restore; new retention is explicit
// through sandbox.pull_outputs or storage.upload_file. This remains the one
// event-type → presentation mapping for old rows in timelines and status chips.
//
// `label` is the verbose form for the event feed; `short` is the compact form
// for the chip on the sandbox table/cards. The `--failed` variant carries the
// danger treatment so possible data loss reads LOUD in both places.
export const PARACHUTE_CHIPS = {
  'sandbox.parachuted':         { variant: 'parachuted', label: 'Results parachuted to cloud',         short: 'Parachuted' },
  'sandbox.parachute_restored': { variant: 'restored',   label: 'Results restored from parachute',     short: 'Restored' },
  'sandbox.parachute_failed':   { variant: 'failed',     label: '⚠ Parachute failed — possible data loss', short: '⚠ Parachute failed' },
};

// Newest legacy parachute event type for an experiment/sandbox, or null.
// Scans the deep events window and matches on either the experiment target or
// the sandbox_id in the payload; picks the latest by id/created_at so feed
// ordering doesn't matter.
export function latestParachute(events, experimentId, sandboxId) {
  let best = null;
  for (const ev of events || []) {
    const type = ev.event_type || ev.type;
    if (!PARACHUTE_CHIPS[type]) continue;
    if (ev.target_id !== experimentId && ev.payload?.sandbox_id !== sandboxId) continue;
    if (!best || String(ev.id ?? ev.created_at ?? '') > String(best.id ?? best.created_at ?? '')) {
      best = ev;
    }
  }
  return best ? (best.event_type || best.type) : null;
}
