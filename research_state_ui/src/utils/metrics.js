// Shared read helpers for metric curves (results_metrics shape).

// Which way is "good" for a metric key: -1 down (loss/error), +1 up
// (accuracy/score), 0 neutral (grad_norm, lr, …).
export function goodDirection(key) {
  if (/loss|err|perplexity|bpb|bpc/i.test(key)) return -1;
  if (/acc|score|reward|f1|auc|mfu/i.test(key)) return 1;
  return 0;
}

// A history entry is [[step, value], …]; pull the finite y-values for charting.
export function curveValues(points) {
  return (Array.isArray(points) ? points : [])
    .map(p => (Array.isArray(p) ? p[1] : null))
    .filter(v => Number.isFinite(v));
}
