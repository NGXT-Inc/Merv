// Fleet liveness: read one /sandboxes row the way a watcher does.
//
// Every field here is already on the row (the command snapshot from the last
// transcript read, the usage series from the control-plane heartbeat sweep), so
// a whole fleet renders live without attaching a terminal to anything. Pure and
// dependency-light on purpose — the desktop table and the mobile card share
// these rules rather than each inventing their own.

// Explicit extension: these rules are covered by `node --test`, which does not
// do Vite's extensionless resolution.
import { fmtDuration } from './format.js';

// Tones are behavioural, not lifecycle: a "running" box can be working, idle,
// or sitting on a failure, and those are three different things to a watcher.
export const FLEET_TONES = ['work', 'fail', 'idle', 'quiet'];

/**
 * What this box is doing right now, in the order a watcher cares about:
 * work in flight, then a failure worth acting on, then idle (money burning),
 * then quiet. Returns null when the row has nothing live to say.
 */
export function fleetActivity(sandbox, now = Date.now()) {
  const s = sandbox || {};
  const command = s.last_command || null;
  const heartbeat = s.heartbeat || null;
  // A control plane that predates the liveness projection omits these keys
  // entirely, rather than sending them empty. Say nothing in that case — the UI
  // ships separately from the backend, and claiming a busy box "has no commands
  // yet" would be worse than showing no liveness line at all.
  if (!('heartbeat' in s) && !('last_command' in s)) return null;
  if (s.status !== 'running') {
    // A finished box still answers "what did it last run" — the bars are gone
    // but the verdict is the reason you'd look at a terminated row at all.
    if (!command) return null;
    return { tone: 'quiet', label: 'last', detail: exitLabel(command) || '—' };
  }

  const started = command?.started_at ? Date.parse(command.started_at) : NaN;
  if (command?.status === 'running') {
    return {
      tone: 'work',
      label: 'running',
      detail: Number.isFinite(started) ? fmtDuration(now - started) : null,
    };
  }

  const idleSince = heartbeat?.idle_since ? Date.parse(heartbeat.idle_since) : NaN;
  const failed = command && command.exit_code != null && command.exit_code !== 0;
  if (failed) {
    return { tone: 'fail', label: 'failed', detail: exitLabel(command) };
  }
  if (Number.isFinite(idleSince)) {
    return {
      tone: 'idle',
      label: `idle ${fmtDuration(now - idleSince)}`,
      detail: exitLabel(command),
    };
  }
  if (!command) return { tone: 'quiet', label: 'no commands yet', detail: null };
  return { tone: 'quiet', label: 'done', detail: exitLabel(command) };
}

/**
 * Up to three utilization bars, chosen by what the box actually has: a GPU box
 * reads GPU / VRAM / RAM, a CPU-only box reads CPU / RAM. A metric the sampler
 * couldn't read is omitted rather than drawn at zero — a blank is honest, a
 * zero bar reads as an idle box.
 */
export function usageBars(latest) {
  if (!latest) return [];
  const bars = [];
  const push = (key, label) => {
    if (Number.isFinite(latest[key])) bars.push({ key, label, pct: latest[key] });
  };
  push('gpu', 'GPU');
  push('vram', 'VRAM');
  if (bars.length < 2) push('cpu', 'CPU');
  push('mem', 'RAM');
  return bars.slice(0, 3);
}

/**
 * The trend series for one metric key. The row tracks whichever metric leads
 * its bars (GPU where there is one), so the line always has a stated subject
 * rather than being an unlabelled squiggle.
 */
export function usageTrend(series, key) {
  if (!key) return [];
  return (series || [])
    .map(point => (point == null ? null : point[key]))
    .filter(value => Number.isFinite(value));
}

function exitLabel(command) {
  if (!command || command.exit_code == null) return null;
  return `exit ${command.exit_code}`;
}
