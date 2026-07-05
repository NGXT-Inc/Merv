/**
 * Per-project MLflow view preferences. Only the metric-direction override
 * lives here today: when no convention or run-declared contract settles
 * which way a metric is good, the user's flip is remembered per project.
 * Kept out of metricProfile.js so the profiler stays pure.
 */
const key = (projectId) => `rsui:mlflow-dir:${projectId}`;

export function readDirectionOverrides(projectId) {
  try {
    return JSON.parse(localStorage.getItem(key(projectId))) || {};
  } catch {
    return {};
  }
}

export function writeDirectionOverride(projectId, metricKey, direction) {
  const next = { ...readDirectionOverrides(projectId), [metricKey]: direction };
  try {
    localStorage.setItem(key(projectId), JSON.stringify(next));
  } catch { /* best-effort */ }
  return next;
}
