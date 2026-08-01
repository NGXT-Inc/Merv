# Hosted client quickstart

Set up a machine that runs agents against the hosted brain while keeping repo
access and caller SSH keys local.

## Install

```bash
git clone https://github.com/NGXT-Inc/Merv.git ~/Merv
```

Agent clients connect directly to the hosted brain over HTTP, so there is no
local proxy process to run. Cloning the repo provides the `merv-client`
onboarding CLI used below. `merv-client`, `merv-http`, the brain, and backend
tests run on Python 3.11+; a project environment is needed only for those
surfaces when 3.11+ is not already available. Sandbox SSH and explicit output
pulls use the system OpenSSH client and `rsync`.

## Authenticate with a key

Each agent client authenticates to the hosted brain with an `mk_` key. Agents
never send a checkout path and the brain never receives one: the project comes
from the credential and the call.

Pick a scope when you mint it:

- **All my projects** (recommended) — one key for every project you belong to,
  on every machine and platform. The agent calls `project(action="list")` and
  passes the `project_id` it wants on each call.
- **One project** — the key is locked to a single project. The agent learns the
  id with `project(action="current")` and may pass no other.

Mint one in the UI:

1. Open [rapidreview.io/merv](https://rapidreview.io/merv) and sign in.
2. Open any project you belong to (an account-scoped key is simply listed here).
3. Create a key, choose its scope, and copy it when shown.

A key is bearer-equivalent to full access to everything it is scoped to, so
treat it like a password. Export it as `MERV_MCP_KEY` rather than storing it in a shared
config, and keep it out of shell history:

```bash
printf 'Paste the project key: '
IFS= read -r -s MERV_MCP_KEY
printf '\n'
export MERV_MCP_KEY
```

Add the `export` to your shell profile (or a `.env` you keep out of git) so
agent sessions inherit it. Never inline the key into a committed config file,
and keep any file that holds it listed in `.gitignore`.

Restart the agent session after changing the key so the MCP connection reloads
it.

## Connect a client

Every agent client — local Claude Code, cloud Codex, Replit, browser-driven —
connects the same way: directly to the brain's `POST /mcp` endpoint with the key
sent as `Authorization: Bearer ${MERV_MCP_KEY}`. Register the plugin in the
client using [CLIENTS.md](CLIENTS.md), then print the ready-to-paste http
snippet for this machine:

```bash
~/Merv/merv/bin/merv-client env
```

It emits the committed-config shape used by `.mcp.json` (and its
`.mcp.codex.json` / `mcp.json` siblings):

```json
{
  "mcpServers": {
    "merv": {
      "type": "http",
      "url": "https://experiments.rapidreview.io/mcp",
      "headers": { "Authorization": "Bearer ${MERV_MCP_KEY}" }
    }
  }
}
```

The key stays in the `MERV_MCP_KEY` env var and is never written into the file,
so the config is safe to commit while the key is not. Start an agent session
from any checkout: the project comes from the credential and the call, so the
same config works from every folder and the checkout path never leaves the
machine.

The snippet points at the hosted brain by default. To target another brain — a
localhost dev brain at `http://127.0.0.1:8787/mcp`, or a self-hosted control
plane — set it once in the machine config so `merv-client env` emits the
matching `url` (or edit the `url` in the snippet directly):

```bash
~/Merv/merv/bin/merv-client configure \
  --control-url https://your-control-plane.example.com
```

## The `merv-client` CLI

The onboarding CLI configures the connection and optional local agent
platforms:

```bash
CLI=~/Merv/merv/bin/merv-client
$CLI configure   # write machine config (e.g. which brain to target)
$CLI env         # print the .mcp.json http snippet for this machine
$CLI agent codex --enable --command codex --parallelism 2
$CLI agent claude --enable --command claude --model opus
$CLI agent hermes --enable --command hermes
$CLI agents      # print the configured local platforms
$CLI workspace --repository /path/to/repo --strategy git_worktree
```

The older `login`, `link`, `links`, `route`, and `unlink` subcommands are gone:
a project-scoped key now carries both authentication and the project binding, so
there is nothing to log in to, link, or unlink.

Agent-platform settings live beside `control_url` in the private
`~/.merv/client.json`. Commands are stored as argv arrays and are never run
through a shell. To let Merv fill a reviewed experiment wave with separate
local sessions:

```bash
~/Merv/merv/bin/merv-agent-runner --project proj_123
```

Native non-interactive process adapters cover Codex, Claude Code, Gemini CLI,
Cursor Agent, OpenCode, Aider, GitHub Copilot CLI, Qwen Code, and Hermes Agent.
A named platform using the `command` adapter can launch another coding agent as
long as it reads the Merv instruction from standard input. The runner removes
its own Merv credentials before launch; each child receives only
`MERV_AGENT_SESSION_KEY`. Codex and Claude Code receive an isolated MCP
configuration for that credential. Hermes and the other agents use
`merv-client call TOOL --arguments JSON`; this bridge reads the same key from
the environment and calls Merv's MCP-shaped endpoint without a shell.

The runner requires Git worktrees. It initializes a Merv-owned bare repository
and central ref, gives each experiment a persistent branch under
`~/.merv/worktrees`, and reuses that branch across agent sessions. Consolidation
has its own persistent worktree; detached reviewer worktrees are temporary.
The user's checkout is never the central ref, and the private bare clone has no
remote, so Merv never pushes to the user's repository. Worktrees prevent Git
collisions; same-user agents can still read one another's files, so use
containers, VMs, or separate OS identities for hostile-agent containment.

To let the hosted Settings page edit the same local file, start the runner's
loopback-only control without dispatching:

```bash
~/Merv/merv/bin/merv-agent-runner --settings-only
```

It prints a generated pairing token, stored owner-only outside `client.json`.
The browser keeps that token in memory and sends it to
`http://127.0.0.1:8791`. The control accepts only paired settings reads/writes
and redacted status; it intentionally has no HTTP start/stop operation because
the settings contain executable argv. Actual agent launch remains the explicit
`merv-agent-runner --project ...` command. Treat the pairing token as
local-administrator authority and paste it only into a trusted Merv UI origin.
