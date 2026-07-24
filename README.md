# Merv

Merv is a plugin for agentic coding platforms that helps agents run machine learning research as gated, reviewable experiment workflows.

It is designed to work with Claude Code, Codex, Cursor, Gemini CLI, OpenCode,
OpenHands, Replit Agent, and other MCP-capable agent platforms. It includes a
frontend for humans to observe agent behavior ranging from macro research
strategy to experiment execution specifics.

The goal is to give research agents enough structure to plan experiments, execute them, review results, and reflect on the project direction to handle open-ended research problems.

## Experiment-level workflow

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/experiment-workflow-dark.svg">
  <img alt="Experiment workflow: Plan, Design review, Execute, Results review, Complete. Rejected reviews send work back to Execute or Plan." src="assets/experiment-workflow-light.svg">
</picture>

Each experiment begins with a generated plan that is adversarially reviewed by another agent. The plan/review loop persists until the reviewer approves the plan. After approval, the agent proceeds to execution. When it is done, it submits a report that is adversarially reviewed by a different agent. The reviewer can send the agent back to execution to fix something in the execution or the report, or it can send it back to the planning stage if the experiment proved faulty.

## Project-level workflow

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/project-workflow-dark.svg">
  <img alt="Project workflow: completed experiments fan out to five reflection lenses, then Synthesis, Reflection review, Publish. Rejected reviews send work back to Synthesis or the fan-out." src="assets/project-workflow-light.svg">
</picture>

After a set of experiments is complete, the plugin drives a project-wide reflection. Five different sub-agents are called, each analyzing the wave's snapshot of all terminal experiments and current claim statuses under a different lens. Their goal is to look for patterns of what works, what does not, and what has not been tried, in order to set up the next phase of experiments. The analysis of the sub-agents is consolidated into a report, logic graph, and change spec. Those artifacts are adversarially reviewed by a different agent for accuracy.

## How the system fits together

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/system-architecture-dark.svg">
  <img alt="System architecture: agent platforms connect directly to the brain over HTTP MCP with a project key; the brain owns durable records and workflow gates and provisions cloud sandboxes; agents run SSH commands and pull retained outputs themselves. The frontend supervises the brain." src="assets/system-architecture-light.svg">
</picture>

Merv has three main pieces:

- **Agent adapters** connect Claude Code, Codex, Cursor, Gemini CLI, OpenCode,
  OpenHands, Replit Agent, and other agentic clients to the same workflow.
- **Backend** owns the research state: projects, claims, experiments, artifacts, review gates, reflections, and sandbox orchestration.
- **Frontend** gives humans a visual way to inspect the project: experiments, reviews, artifacts, logic graphs, timelines, and current progress.

By default the plugin connects to the hosted brain; it can also run fully
locally. In either deployment the checkout root and caller SSH private keys
stay on the user's machine. Agents send explicit project ids, typed metadata,
and selected submitted bytes; the brain never opens the checkout directly.
Brain management keys remain separate operational credentials.

## Set up

Connect any agent platform to the **hosted brain** in two steps: mint a project
key, then register the endpoint. No proxy, no daemon, no `pip` install. Running
your own brain instead? See [Self-hosting](#self-hosting).

### 1. Mint a project key

At [rapidreview.io/map](https://rapidreview.io/map): sign in, open or create a
project, and mint a key (shown once). Export it where the agent runs:

```bash
export MERV_MCP_KEY=mk_...
```

One key binds one project and is bearer-equivalent to full access — treat it
like a password. Browser platforms (claude.ai, Replit) use OAuth instead.

### 2. Connect your platform

**Claude Code** — then restart:

```bash
claude plugin marketplace add https://rapidreview.io/marketplace.json
claude plugin install merv@rapidreview
```

**Codex CLI** — plugin (skills + reviewers), then wire the key:

```bash
codex plugin marketplace add NGXT-Inc/Merv
codex plugin add merv@rapidreview
codex mcp add merv --url https://experiments.rapidreview.io/mcp --bearer-token-env-var MERV_MCP_KEY
```

**OpenHands** (CLI):

```bash
openhands mcp add merv --transport http --header "Authorization: Bearer $MERV_MCP_KEY" https://experiments.rapidreview.io/mcp
```

**Gemini CLI** — from a checkout: `gemini extensions install /path/to/merv`.

**OpenCode** — from a checkout: run `merv/clients/opencode/install.sh`, then add the `opencode.json` block it prints.

**Replit** / **claude.ai** — add a custom MCP server at `https://experiments.rapidreview.io/mcp` and approve the OAuth sign-in.

**Cursor** — no local-plugin registry, so build the client bundle straight into
Cursor's local-plugin dir, then enable **merv** on Cursor's Customize page:

```bash
git clone https://github.com/NGXT-Inc/Merv.git ~/Merv
python3 ~/Merv/merv/scripts/build_client_bundle.py --out ~/.cursor/plugins/local/merv
```

Re-run the same command (after `git -C ~/Merv pull`) to update.

Full per-platform notes: [CLIENTS.md](merv/docs/CLIENTS.md).

### 3. First run

Ask the agent to call `project(action="current")` for its bound project id,
then `workflow.status_and_next(project_id)`. Follow along at
[rapidreview.io/merv](https://rapidreview.io/merv).

## Self-hosting

The hosted brain runs this repo's code, and you can run the whole stack — brain,
Postgres, S3-compatible store, MLflow — yourself. Start from the reference
deployment in [merv/deploy/README.md](merv/deploy/README.md); operations are in
[CONTROL_PLANE_OPERATIONS.md](merv/docs/CONTROL_PLANE_OPERATIONS.md). Clients
connect the same way — point the MCP `url` at your own brain.

## Migrating from Research Suite (`research-plugin`)

Upgrading from the old `research-plugin`? Everything was renamed in v0.0012
and the hosted brain now requires sign-in, but your data carries over
untouched. See [MIGRATING.md](MIGRATING.md) for the per-client steps
(Claude Code, Cursor, Codex).
