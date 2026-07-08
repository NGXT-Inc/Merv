# Research Plugin

Research Plugin gives agentic coding clients (Claude Code, Codex, Cursor,
Gemini CLI, OpenCode) a shared state machine for machine learning research:
claims, experiments, repo-file resources, review gates, reflection waves, and
sandboxed execution. A hosted brain owns all durable state; a small stdio MCP
proxy runs on your machine and does the repo-local file work — the brain never
sees your checkout.

## Get started

```bash
git clone <research-suite-repo-url> ~/research-suite
```

That is the whole install — the proxy runs on bare `python3` (3.11+), no pip
installs. Then:

1. Register the plugin in your client — per-client steps in
   [docs/CLIENTS.md](docs/CLIENTS.md).
2. Open your research repo and start a session:

```text
Use Research Plugin. Start with project.current, then workflow.status_and_next.
```

The proxy dials the hosted brain by default. On first use the agent asks which
project this folder belongs to and links it with `project.connect` — no
terminal setup. Details and the CLI fallback:
[docs/HOSTED_CLIENT_QUICKSTART.md](docs/HOSTED_CLIENT_QUICKSTART.md).

## How work moves

```text
Experiments:  planned -> design_review -> ready_to_run -> running
              -> experiment_review -> complete
Reflections:  reflecting -> synthesizing -> reflection_review -> published
```

Artifacts are regular files in your repo; the backend records repo-relative
paths and pinned versions, and review gates check the submitted snapshot.

## Running a local brain (optional)

For development, or to keep all state on your machine:

```bash
cd /path/to/research_plugin
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./bin/research-plugin-http --host 127.0.0.1 --port 8787 --activity-stderr
bin/research-plugin-client configure --control-url http://127.0.0.1:8787
```

Sandbox provider credentials (Lambda Labs by default; Thunder, Modal, and a
fake test backend via `RESEARCH_PLUGIN_EXECUTION_BACKEND`) belong to the brain
process only — see `.env.example`. Startup details:
[docs/STARTUP_CHEATSHEET.md](docs/STARTUP_CHEATSHEET.md).

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests
```

Set `RESEARCH_PLUGIN_EXECUTION_BACKEND=fake` to keep tests and local workflows
off cloud providers.

## Documentation

- [docs/CLIENTS.md](docs/CLIENTS.md) - per-client install and reviewer handoff
- [docs/HOSTED_CLIENT_QUICKSTART.md](docs/HOSTED_CLIENT_QUICKSTART.md) - hosted setup
- [docs/STARTUP_CHEATSHEET.md](docs/STARTUP_CHEATSHEET.md) - local startup flow
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - backend and mode architecture
- [docs/MCP_SERVER_CONTRACT.md](docs/MCP_SERVER_CONTRACT.md) - MCP tools and contracts
- [docs/WORKFLOW_AND_REVIEW.md](docs/WORKFLOW_AND_REVIEW.md) - workflow gates and reviews
- [docs/RESOURCE_MODEL.md](docs/RESOURCE_MODEL.md) - repo-file resource model
- [docs/CENTRALIZED_MLFLOW.md](docs/CENTRALIZED_MLFLOW.md) - centralized MLflow tracking
- [docs/UI_API.md](docs/UI_API.md) - frontend HTTP API
- [deploy/README.md](deploy/README.md) - reference control-plane deploy
