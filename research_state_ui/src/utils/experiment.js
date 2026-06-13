// The experiment's display identity: the short unique name (also its folder
// name under experiments/). Experiments that predate the name requirement
// fall back to their id.
export function expName(exp) {
  return (exp?.name || '').trim() || exp?.id || '';
}

// Statuses where an experiment is done evolving — the figure/logic-graph
// canvases stop polling once an experiment reaches one of these.
export const TERMINAL_STATUSES = ['complete', 'failed', 'abandoned'];
