import assert from 'node:assert/strict';
import test from 'node:test';

import { fleetActivity, usageBars, usageTrend } from './fleet.js';

const NOW = Date.parse('2026-08-01T12:00:00Z');
const running = (extra = {}) => ({ status: 'running', ...extra });
const ago = (minutes) => new Date(NOW - minutes * 60_000).toISOString();

test('a command in flight reads as work, with its elapsed time', () => {
  const activity = fleetActivity(
    running({ last_command: { status: 'running', started_at: ago(8) } }),
    NOW,
  );
  assert.equal(activity.tone, 'work');
  assert.equal(activity.label, 'running');
  assert.equal(activity.detail, '8m');
});

test('a quiet box surfaces how long it has been burning money', () => {
  const activity = fleetActivity(
    running({
      last_command: { status: 'finished', exit_code: 0 },
      heartbeat: { idle_since: ago(22) },
    }),
    NOW,
  );
  assert.equal(activity.tone, 'idle');
  assert.equal(activity.label, 'idle 22m');
  assert.equal(activity.detail, 'exit 0');
});

test('a failure outranks idle so the alarm colour wins, keeping both facts', () => {
  const activity = fleetActivity(
    running({
      last_command: { status: 'finished', exit_code: 1 },
      heartbeat: { idle_since: ago(22) },
    }),
    NOW,
  );
  assert.equal(activity.tone, 'fail');
  assert.equal(activity.detail, 'exit 1');
});

test('a running command is never reported as idle', () => {
  const activity = fleetActivity(
    running({
      last_command: { status: 'running', started_at: ago(2) },
      heartbeat: { idle_since: ago(30) },
    }),
    NOW,
  );
  assert.equal(activity.tone, 'work');
});

test('a fresh box says so rather than implying a finished run', () => {
  // The projection always sends `heartbeat` for a running row, even empty.
  assert.equal(fleetActivity(running({ heartbeat: null }), NOW).label, 'no commands yet');
});

test('a row from a backend without the projection claims nothing at all', () => {
  // The UI ships separately from the control plane. Absent keys mean "this
  // server cannot tell me", which must not render as "this box is idle".
  assert.equal(fleetActivity(running(), NOW), null);
  assert.equal(fleetActivity({ status: 'terminated' }, NOW), null);
});

test('a terminated row keeps its verdict and claims no liveness', () => {
  const activity = fleetActivity(
    { status: 'terminated', last_command: { status: 'finished', exit_code: 137 } },
    NOW,
  );
  assert.equal(activity.tone, 'quiet');
  assert.equal(activity.detail, 'exit 137');
  assert.equal(fleetActivity({ status: 'terminated' }, NOW), null);
});

test('a gpu box reads gpu/vram/ram; a cpu-only box reads cpu/ram', () => {
  assert.deepEqual(
    usageBars({ gpu: 94, vram: 61, cpu: 50, mem: 38 }).map(b => b.label),
    ['GPU', 'VRAM', 'RAM'],
  );
  assert.deepEqual(
    usageBars({ gpu: null, vram: null, cpu: 50, mem: 38 }).map(b => b.label),
    ['CPU', 'RAM'],
  );
});

test('an unreadable metric is omitted rather than drawn at zero', () => {
  // A zero bar would read as an idle box and could talk someone into
  // releasing live work.
  assert.deepEqual(usageBars({ gpu: null, cpu: null, mem: 12 }).map(b => b.label), ['RAM']);
  assert.deepEqual(usageBars(null), []);
});

test('the trend tracks one named metric and drops gaps', () => {
  const series = [{ gpu: 10 }, { gpu: null }, { gpu: 90 }, {}];
  assert.deepEqual(usageTrend(series, 'gpu'), [10, 90]);
  assert.deepEqual(usageTrend(series, undefined), []);
  assert.deepEqual(usageTrend(null, 'gpu'), []);
});
