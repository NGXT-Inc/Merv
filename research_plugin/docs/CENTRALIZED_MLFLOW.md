# Centralized MLflow

**Status:** implemented  
**Updated:** 2026-06-22

MLflow is backend-owned infrastructure. New runs log to one centralized MLflow
tracking service for the backend deployment; sandboxes are MLflow clients only.

## Decisions

- Run one MLflow server per backend deployment, not one per project.
- Namespace runs as `rp/<project_id>/<experiment_id>`.
- Keep stable IDs in MLflow names; human names belong in tags.
- Keep TensorBoard separate and sandbox-local for now.
- Keep old pulled `mlflow.db` support only as a legacy metrics fallback.
- Add auth later at the same endpoint/env-injection boundary.

## Deployment Modes

### Hosted / Remote Backend

The backend VM runs MLflow beside the control server.

```text
remote sandbox -> RESEARCH_PLUGIN_MLFLOW_TRACKING_URI / public MLflow URL
backend        -> RESEARCH_PLUGIN_MLFLOW_SERVER_URI / internal MLflow URL
```

The compose stack starts:

- `control`
- `mlflow`
- Postgres databases for backend state and MLflow
- MinIO buckets for backend blobs and MLflow artifacts

### Local Backend

If no external MLflow URI is configured, the local HTTP backend starts one
managed MLflow process under the registry state directory.

Local processes use the local URL directly:

```text
MLFLOW_TRACKING_URI=http://127.0.0.1:<port>
```

Remote sandboxes controlled by the local backend use a daemon-owned reverse SSH
tunnel for that same loopback URI:

```bash
ssh -N -R 127.0.0.1:<port>:127.0.0.1:<port> <sandbox>
```

The daemon owns the tunnel lifecycle: create on sandbox access, reuse while
alive, stop on release/teardown/shutdown.

## Configuration

```bash
RESEARCH_PLUGIN_MLFLOW_MODE=external
RESEARCH_PLUGIN_MLFLOW_TRACKING_URI=https://backend.example.com/mlflow
RESEARCH_PLUGIN_MLFLOW_SERVER_URI=http://mlflow:5000
RESEARCH_PLUGIN_MLFLOW_DASHBOARD_URL=https://backend.example.com/mlflow
```

- `TRACKING_URI`: what agents/training code use.
- `SERVER_URI`: optional backend-internal route for snapshots.
- `DASHBOARD_URL`: what users open.
- `managed`: local backend starts MLflow itself.
- `external`: backend points at an existing MLflow service.

## Agent Contract

`sandbox.request` and `sandbox.get` return an `mlflow` block and the sandbox
environment exports the same values:

```json
{
  "mlflow": {
    "configured": true,
    "tracking_uri": "https://backend.example.com/mlflow",
    "experiment_name": "rp/proj_123/exp_456",
    "dashboard_url": "https://backend.example.com/mlflow",
    "env": {
      "MLFLOW_TRACKING_URI": "https://backend.example.com/mlflow",
      "MLFLOW_EXPERIMENT_NAME": "rp/proj_123/exp_456"
    }
  }
}
```

Agents should use those env vars for quantitative experiments. They should not
start MLflow servers in sandboxes.

## Metrics

`/results/metrics` snapshots the centralized MLflow experiment into durable
backend state. If no central snapshot is available, the old pulled
`mlflow.db` reader remains as compatibility for pre-centralization runs.

Snapshots strip internal server URLs before UI/API exposure.

## Failure Policy

MLflow is best-effort for now:

- If configured and reachable, inject it and snapshot centrally.
- If unreachable, report readiness in `sandbox.get` and health output.
- Training is not blocked solely because MLflow is down.

Future user auth should scope the same environment injection point.
