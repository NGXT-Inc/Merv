# research_plugin

Lean Codex plug-in architecture for research state.

Current version: `0.0005`.

This project starts fresh. It does not port the existing `research_state_mockup/backend`
implementation. The goal is to keep the durable model small:

- claims: what we think
- experiments: what we try
- resources: regular files in the local repo

Codex owns local reasoning, editing, lightweight scripts, and reviewer-agent
delegation. The backend (an HTTP daemon, fronted to Codex by a stdio MCP
proxy) owns mutation permissions, workflow state, durable memory, review
gates, and Modal sandbox provisioning for ML execution.

## First reduction

The first deliberate simplification is the resource model:

> one repo file maps to one resource.

The server stores a repo-relative file path plus append-only observed versions.
Each version captures size, mtime, content sha256, and mimetype — but not the
file contents. Historical content is whatever the user's repo still has on
disk or in their own git history. The plugin does not need artifact refs,
manifests, previews, or cache directories for the MVP.

Project scope is directory-backed. The shared backend can serve many projects,
but each MCP proxy is started inside one project folder and forwards that repo
root as hidden context. Agents should call `project.current` first; project-
scoped MCP schemas hide `project_id` when the folder supplies it.

See [docs/RESOURCE_MODEL.md](docs/RESOURCE_MODEL.md).

## Architecture

The plugin runs as **one long-lived HTTP daemon** plus a **thin stdio MCP
proxy** that Codex spawns on demand. The daemon owns SQLite state, the
activity log, the sandbox execution backend, and the volume sync poller. The MCP proxy is stateless — it forwards `tools/list` and `tools/call`
to the daemon's `/mcp/*` endpoints. Both the browser UI and Codex go through
the same daemon, eliminating the cross-process race the old split-brain setup
had. **Start the daemon before opening Codex.**

- [docs/STARTUP_CHEATSHEET.md](docs/STARTUP_CHEATSHEET.md) - local startup commands for the daemon, Codex, sandboxes, and activity logs
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - full plug-in architecture
- [docs/MCP_SERVER_CONTRACT.md](docs/MCP_SERVER_CONTRACT.md) - MCP tools and state ownership
- [docs/UI_API.md](docs/UI_API.md) - lightweight HTTP API for frontend work
- [docs/WORKFLOW_AND_REVIEW.md](docs/WORKFLOW_AND_REVIEW.md) - experiment workflow and review gates
- [docs/REVIEW_IDENTITY.md](docs/REVIEW_IDENTITY.md) - local reviewer identity model
- [docs/CLAUDE_FRONTEND_HANDOFF.md](docs/CLAUDE_FRONTEND_HANDOFF.md) - context for rebuilding the UI

## Plugin contents

- `.codex-plugin/plugin.json` - Codex plug-in manifest (references `.mcp.codex.json`)
- `.claude-plugin/plugin.json` - Claude Code plug-in manifest
- `.mcp.json` - Claude Code MCP server registration, portable via `${CLAUDE_PLUGIN_ROOT}`
- `.mcp.codex.json` - Codex MCP server registration with absolute install path
- `.env.example` - template for the per-user credentials file (see "Use with Claude Code" below)
- `pyproject.toml` - package metadata, dependency declaration, `console_scripts`
- `backend/` - HTTP daemon code: services, SQLite state, activity log, execution backends, volume sync (Python package `backend`)
- `mcp_server/` - thin stdio MCP proxy that forwards tool calls to the daemon (Python package `mcp_server`)
- `bin/research-plugin-mcp` - launcher for the stdio MCP proxy
- `bin/research-plugin-http` - launcher for the HTTP daemon
- `skills/research-workflow/SKILL.md` - primary operating skill (Codex + Claude Code)
- `skills/design-review/SKILL.md` - read-only design review skill (Codex spawn path)
- `skills/experiment-review/SKILL.md` - read-only full experiment review skill (Codex spawn path)
- `agents/design-review.md` - Claude Code subagent for read-only design review (`research-plugin:design-review`)
- `agents/experiment-review.md` - Claude Code subagent for read-only experiment review (`research-plugin:experiment-review`)

## v0.0005 server

The server uses Pydantic for tool contracts and FastAPI for the UI-facing HTTP
adapter.

Install core backend dependencies in a plugin-local virtualenv:

```bash
cd /path/to/research_plugin
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

The launchers use `.venv/bin/python` automatically when that virtualenv exists.
Set `RESEARCH_PLUGIN_PYTHON=/path/to/python` to force a different interpreter.

Run tests:

```bash
PYTHONPATH=research_plugin research_plugin/.venv/bin/python -m unittest discover -s research_plugin/tests -v
```

Start the shared HTTP daemon **first**. It owns the long-lived process and routes
each project to that project's local directory:

```bash
/path/to/research_plugin/bin/research-plugin-http --host 127.0.0.1 --port 8787
```

The legacy single-repo mode is still available with
`--repo /path/to/research-repo`. In shared mode, the UI creates a project by
providing a directory; that directory owns its own `.research_plugin/state.sqlite`,
sync state, sandbox keys, and files. The backend stores only a small global
registry that maps project ids to directories. The mapping is one-to-one: one
project per directory, and one directory per project.

Then Codex (or any other caller) can launch the stdio MCP proxy from inside
the research repo. The MCP proxy stays project-local: it forwards the repo root
as hidden context to the shared daemon, so the agent does not see extra routing
fields or a larger tool schema.
Through MCP, agents should call `project.current`. It returns the folder's
project or `exists: false` with a hint to call `project.create`. It does not
expose or create projects from other folders. The older `project.list` tool is
kept for HTTP/internal compatibility but is not advertised to MCP agents.

```bash
cd /path/to/research-repo
/path/to/research_plugin/bin/research-plugin-mcp
```

The marketplace MCP config points the project-local proxy at
`http://127.0.0.1:8787` by default, so a fresh folder can call `project.current`
before it has a marker. Set `RESEARCH_PLUGIN_DAEMON_URL` only when the shared
daemon is on another host or port. Once a directory-backed project has been
registered, the daemon also writes that directory's `.research_plugin/daemon.json`
marker for discovery. The MCP proxy never opens SQLite, spawns sandboxes, or
writes activity itself — those all happen inside the daemon.

Run the HTTP API with auto-reload during backend development:

```bash
python3 scripts/dev_http_reload.py \
  --host 127.0.0.1 \
  --port 8787 \
  --activity-stderr
```

The reload helper watches the plugin backend source and starts the same shared
multi-project backend as `research-plugin-http`. If port `8787` is already
occupied, stop the existing HTTP process or use another port. Pass `--repo
/path/to/research-repo` only for the legacy single-repo backend.

Activity is also appended to:

```text
<project-dir>/.research_plugin/activity.jsonl
```

Use that file to watch both HTTP API activity and Codex-started MCP tool calls:

```bash
tail -f /path/to/research-repo/.research_plugin/activity.jsonl
```

All activity (UI calls and Codex MCP tool calls) flows through the same daemon
process, so terminal-side `--activity-stderr` and `/api/activity` both see
everything. The JSONL file still works as a cross-tool tail.

For local Codex plugin development, register the parent repo marketplace:

```bash
codex plugin marketplace add /Users/guraltoo/Documents/dev/proj/experiments/Papyrus
```

```text
.agents/plugins/marketplace.json
```

It points to `./research_plugin`. After installation, plugin state is stored in
the active research repo at `.research_plugin/state.sqlite`, not beside the
plugin code.

### Use with Claude Code

The plugin ships as a Claude Code marketplace plugin. The marketplace lives at
the repo root (`../.claude-plugin/marketplace.json`); the plugin manifest lives
at `.claude-plugin/plugin.json` inside this directory. Skills, subagents, and
the MCP server config (`.mcp.json` with `${CLAUDE_PLUGIN_ROOT}` path placeholder)
all sit at plugin root and are auto-discovered by Claude Code.

**End-user install** (inside Claude Code):

```text
/plugin marketplace add <git-host>/research-suite
/plugin install research-plugin@research-suite
```

For an in-tree local install during development:

```bash
claude --plugin-dir /path/to/research_plugin
```

The MCP proxy that Claude Code spawns ([bin/research-plugin-mcp](bin/research-plugin-mcp))
is **stdlib-only** — it does not need a venv or any Python dependencies, so
there is no install step on the Claude Code path. The HTTP daemon
([bin/research-plugin-http](bin/research-plugin-http)) is what actually needs
`requirements.txt`, and the user starts the daemon manually once per machine
(see below).

Once installed, drop your Modal / Hugging Face credentials at a per-user
location **outside the plugin tree** (the plugin source must never contain
real secrets — see "Credentials" below), then run the daemon once per machine:

```bash
${CLAUDE_PLUGIN_ROOT}/bin/research-plugin-http &
```

**Credentials.** [bin/research-plugin-http](bin/research-plugin-http) resolves
the env file in this priority, first hit wins:

1. `$RESEARCH_PLUGIN_MODAL_ENV_FILE` (explicit deployment override)
2. `${CLAUDE_PLUGIN_DATA}/.env`
3. `${XDG_CONFIG_HOME:-$HOME/.config}/research-plugin/.env`  ← **recommended**
4. `$HOME/.research_plugin/.env`
5. `$PLUGIN_DIR/.env` (dev-only, gitignored)

If none exist, the Modal SDK falls back to its native `~/.modal.toml`. To set
up the recommended location:

```bash
mkdir -p ~/.config/research-plugin
cp /path/to/research_plugin/.env.example ~/.config/research-plugin/.env
chmod 700 ~/.config/research-plugin
chmod 600 ~/.config/research-plugin/.env
# then fill in MODAL_TOKEN_ID, MODAL_TOKEN_SECRET, HF_TOKEN
```

Why this layout matters: `claude plugin install` (with a `directory`-source
marketplace) copies the entire plugin source into
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, including any
files at the source root. A `.env` left in the plugin tree would be copied
into every install. The launcher's user-config lookup means real secrets stay
in `~/.config/research-plugin/` and never enter the plugin tree at all. The
shipped `.env.example` documents which keys are required, with empty values.

Then open any research project — Claude Code spawns the stdio MCP server with
`$PWD` set to the project root, so `RESEARCH_PLUGIN_REPO_ROOT` resolves
correctly and the shared daemon routes to that project's
`.research_plugin/state.sqlite`.

**Approval**: the `default_tools_approval_mode` field is Codex-only and is
absent from the Claude Code `.mcp.json`. Configure approval through
`.claude/settings.json` (allowlist `mcp__research-plugin__*`) or accept the
in-session `/permissions` prompts.

**Reviewer handoff**: when `workflow.status_and_next` returns
`launch_design_reviewer` or `launch_experiment_reviewer`, the orchestrator
calls the Agent tool with `subagent_type` set to
`research-plugin:design-review` or `research-plugin:experiment-review` and
passes `experiment_id`, `review_request_id`, and `reviewer_capability` in the
prompt. The subagent calls `review.start` with the capability, then
`review.submit` with the structured verdict. The skill name returned by the
daemon (`design-review` / `experiment-review`) matches the subagent file name;
the `research-plugin:` namespace prefix is added by Claude Code's plugin
loader.

The Codex install path is unchanged. Its absolute-path `.mcp.codex.json` lives
beside the Claude Code `.mcp.json`; `.codex-plugin/plugin.json` references the
Codex-specific file so both clients coexist without stepping on each other.

#### Updating after source changes

The plugin cache is a snapshot taken at install time, so edits to the source
do not appear live in Claude Code. Refresh both the marketplace metadata and
the plugin snapshot with:

```bash
claude plugin marketplace update research-suite
claude plugin uninstall research-plugin@research-suite && claude plugin install research-plugin@research-suite
```

`claude plugin update research-plugin@research-suite` only re-runs when the
declared `version` in [.claude-plugin/plugin.json](.claude-plugin/plugin.json)
changes. While iterating on the same version, the uninstall + reinstall pair
above is the clean re-snapshot. No venv work runs on session start — the MCP
proxy is stdlib-only.

## Local shipping

`research-plugin` is designed to be installed once and used from arbitrary
research repos:

```text
installed plugin code
  /path/to/research_plugin

target research repo
  /path/to/my-ml-project
  .research_plugin/state.sqlite
  local files used as resources
```

The MCP launcher resolves its own install directory, adds
`<plugin_dir>` to `PYTHONPATH` (so the `mcp_server` package is importable),
and defaults the repo for daemon discovery to the current working directory:

```text
RESEARCH_PLUGIN_REPO_ROOT=$PWD
```

The MCP proxy uses that to locate `.research_plugin/daemon.json`. Set
`RESEARCH_PLUGIN_DAEMON_URL` to override discovery entirely. State paths
(`RESEARCH_PLUGIN_STORE` and `RESEARCH_PLUGIN_REGISTRY_STORE`) are only consumed
by the HTTP daemon — pass them to `research-plugin-http`, not to the MCP
launcher.

```bash
/path/to/research_plugin/bin/research-plugin-http --port 8787
```

For this local marketplace install, `.mcp.codex.json` uses the absolute path
to `bin/research-plugin-mcp`. Codex starts MCP from the active research repo,
so a relative `./bin/...` command would incorrectly point into that repo, and
Codex does not substitute `${CLAUDE_PLUGIN_ROOT}` the way Claude Code does.
The Claude Code config at `.mcp.json` uses `${CLAUDE_PLUGIN_ROOT}` so it
remains portable across installs.

The MCP config also sets `default_tools_approval_mode` to `approve` for this
local plugin. That is a Codex client approval setting, not a backend permission
check. The backend still enforces its own workflow and reviewer permissions, and
Codex/user config can override tool approval behavior.

Fresh Codex session smoke checklist:

1. Open a research repo that does not contain the plugin source.
2. Use `/plugins` and enable `research-plugin` from `Papyrus Local Plugins`.
3. Ask Codex to use the research workflow skill.
4. Confirm `workflow.status_and_next` works with an explicit `project_id`.
5. Create a claim and experiment.
6. Write a local plan/result file in the research repo.
7. Register the file through MCP as a resource.
8. Confirm `.research_plugin/state.sqlite` exists in the research repo, not in
   the plugin install directory.
9. Run design and experiment review.
10. Confirm stale resources/reviews do not satisfy later attempts.

## Sandbox execution engine

There is no job abstraction. The agent **requests a sandbox** for an experiment
and runs shell commands on it directly over SSH. Modal is the default backend.
The agent calls `sandbox.request` / `sandbox.get` / `sandbox.terminal` /
`sandbox.release`; it never talks to Modal directly.

Provisioning is **best-effort-synchronous**: creating a sandbox (large first
sync, cold GPU) can outlast the MCP call timeout, so `sandbox.request`
provisions on a background thread and waits up to a budget (default 45s,
`RESEARCH_PLUGIN_SANDBOX_REQUEST_WAIT`). If it comes up in time you get
`status: running` with `ssh.command` inline; otherwise you get
`status: provisioning` and **poll `sandbox.get`** (read-only) until it is
`running` or `failed`. `get` reconciles a provisioning row whose job died
(daemon restart) to `failed`, so a poll loop always terminates; the sandbox id
is persisted the instant the sandbox is created and a partial failure terminates
it, so a timed-out or canceled request never orphans a Modal sandbox.

For Modal, make sure `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` are available to
the **HTTP daemon process** (the MCP proxy does not need them). The simplest way
is a git-ignored `.env` at the plugin root — `research_plugin/.env` — which the
daemon auto-detects:

```bash
# research_plugin/.env  (git-ignored)
MODAL_TOKEN_ID=...
MODAL_TOKEN_SECRET=...
```

Resolution order: `RESEARCH_PLUGIN_MODAL_ENV_FILE` (if set) → `research_plugin/.env`
→ variables already exported in the environment (which always win). So you can
also point at a file elsewhere or export the tokens directly:

```bash
export RESEARCH_PLUGIN_MODAL_ENV_FILE=/path/to/backend/.env
```

`SandboxService` is the central registry: **one sandbox per experiment**,
reuse-if-alive-else-create. On `sandbox.request` it generates a per-experiment
ed25519 keypair, creates a Modal sandbox with the project Volume v2 mounted and
`openssh-server` running, exposes SSH over an unencrypted Modal tunnel
(`unencrypted_ports=[22]`), authorizes the public key, and returns SSH details.
The repo is mounted at `/workspace/repo`. Large datasets and caches should be
downloaded to `sandbox_data_dir` (default `/workspace/sandbox_data`, also exposed
inside SSH commands as `$RP_SANDBOX_DATA_DIR` and `$RP_DATASET_DIR`), which is
sandbox-local ephemeral storage outside the Modal Volume.
If `HF_TOKEN` is present in the backend `.env` or process environment, sandbox
creation passes it with `modal.Secret.from_local_environ(["HF_TOKEN"])`, while
non-secret bootstrap values use `Sandbox.create(env=...)`. The SSH wrapper then
exports both `HF_TOKEN` and `HUGGING_FACE_HUB_TOKEN` for Hugging Face tooling.
The token is never returned to agents; sandbox responses only advertise that the
env var is available.

To keep agent commands short, the registry also drops a static dispatcher at
`.research_plugin/sbx` and a per-experiment connection file under
`.research_plugin/sandboxes/conn/<experiment_id>` (regenerated each request,
since the host/port change). The agent runs
`.research_plugin/sbx <experiment_id> '<command>'` instead of a ~210-character
`ssh` line; the response's `ssh.command` is that short form and `ssh.raw_command`
is the full `ssh` invocation for use outside the repo root. Releasing or expiring
a sandbox removes the conn file so the dispatcher fails loudly rather than
connecting to a recycled host:port.

Visibility: an in-sandbox `sshd` `ForceCommand` wrapper records every command and
its output to `.research_plugin_sessions/<experiment>/transcript.log` on the
mounted Volume. `sandbox.terminal` reads it (live from the sandbox, falling back
to the committed Volume); the UI renders it as a per-experiment terminal window.

The Modal backend mirrors each project's local repo into a per-project Modal
Volume v2 (`research-plugin-<project_id>`) and mounts it writable at
`/workspace/repo`. A `SyncEngine` keeps the Volume and the local repo in
agreement via a three-way diff against a baseline stored at
`.research_plugin/modal/sync.sqlite`. It runs:

- on `project.create` (initial volume + baseline registration),
- on `sandbox.request` (push current repo before the sandbox boots),
- and every 60 s in a background poller (bidirectional, pulling committed
  sandbox writes back to the local repo).

`sandbox.sync` is the explicit live-sandbox visibility boundary: it runs Modal's
Volumes v2-only `sync <workdir>` command inside the sandbox to commit mounted
repo writes, then runs the daemon sync pass to pull committed files into the
local repo. `RESEARCH_PLUGIN_MODAL_VOLUME_VERSION` defaults to `2` and any other
value is rejected. Modal does not auto-migrate existing v1 Volumes; old project
Volumes must be recreated or manually migrated before this command can work.
Volumes v2 are beta, and Modal's current limits still apply: files must be under
1 TiB, a single directory can contain at most 262,144 files, and concurrent
writes to the same file are last-write-wins.

Conflicts (both sides changed since the last sync) are recorded and halt the
next `sandbox.request` until resolved.

Excluded paths are configurable per project in the UI/API and fall back to
`.research_plugin/sync_exclusions.json`, which is created with the current
defaults on startup. The config has three lists: `names` (path components
excluded anywhere), `prefixes`/`paths` (repo-relative path prefixes), and
`suffixes`. Defaults: `.git`, `.research_plugin`, `.research_plugin_sessions`,
`.venv`/`venv`, `__pycache__`, `*.pyc`, `.mypy_cache`, `.pytest_cache`,
`.ruff_cache`, `.cache`, `.aws`, `node_modules`, `.DS_Store`, `data/raw`,
`data/processed`. These exclusions prevent local sync pollution, but paths under
the mounted repo still occupy the Modal Volume; use `sandbox_data_dir` for large
downloaded datasets.

Implemented MCP tools:

- `workflow.status_and_next`
- `project.current`, `project.create`, `project.update`, `project.get`, `project.get_settings`, `project.update_settings`
- `claim.create`, `claim.list`
- `experiment.create`, `experiment.list`, `experiment.get_state`, `experiment.transition`
- `resource.register_file`, `resource.observe_file`, `resource.sync_changed_files`, `resource.associate`, `resource.list`, `resource.resolve`, `resource.history`
- `review.request`, `review.start`, `review.submit`, `review.status`
- `sandbox.request`, `sandbox.get`, `sandbox.sync`, `sandbox.list`, `sandbox.release`, `sandbox.terminal`, `sandbox.health`
