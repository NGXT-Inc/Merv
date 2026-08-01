# Hermes Agent

Hermes can use Merv's canonical Agent Skills and native remote HTTP MCP
connection. It also works as a local platform for `merv-agent-runner`.

## Load the skills

The most portable setup is to keep Merv's canonical skills read-only and add
their absolute parent directory to Hermes:

```yaml
skills:
  external_dirs:
    - /absolute/path/to/merv/skills
```

Hermes expands `~` and `${ENV_VAR}` in external skill paths. Local Hermes
skills win if the same name exists in both locations.

On POSIX systems, the bundled installer is a convenient alternative:

From the Merv plugin or slim-client bundle:

```bash
./clients/hermes/install.sh
```

The installer symlinks every canonical `skills/<name>/SKILL.md` directory into
`${HERMES_HOME:-$HOME/.hermes}/skills`. It refuses to replace a real existing
skill directory. Keep the source bundle read-only if Hermes should not be able
to update those shared files. On Windows, use `skills.external_dirs` instead
of the shell installer.

## Connect native MCP

Add either authentication form beneath `mcp_servers` in
`~/.hermes/config.yaml` (or `$HERMES_HOME/config.yaml`).

Use a bearer key for headless or CI machines:

```yaml
mcp_servers:
  merv:
    url: "https://experiments.rapidreview.io/mcp"
    headers:
      Authorization: "Bearer ${MERV_MCP_KEY}"
```

Hermes expands `${ENV_VAR}` references at runtime, so export `MERV_MCP_KEY`
before starting it.

For an interactive user profile, native OAuth avoids managing a bearer key:

```yaml
mcp_servers:
  merv:
    url: "https://experiments.rapidreview.io/mcp"
    auth: oauth
```

Then run:

```bash
hermes mcp login merv
```

Hermes exposes remote tools as `mcp_<server>_<tool>`. For example,
`workflow.status_and_next` is available as
`mcp_merv_workflow_status_and_next`. Apply that same translation to every
public tool named by a canonical skill or handoff prompt—for example,
`review.start` becomes `mcp_merv_review_start`. Runner-owned Hermes sessions
use `merv-client call` with the original public tool name instead.

## Reviews and reflection

After `review.request`, pass `reviewer_handoff.spawn_prompt` unchanged to a
fresh `delegate_task` child. The child must call `review.start` with its own
non-empty `caller_session_id`; the producer must not submit its own review.

For a project-reflection wave, launch the five independent lens prompts with
`delegate_task(tasks=[...])`. Hermes defaults to three concurrent delegated
tasks, so five lenses normally run in two waves unless the user changes the
delegate-task concurrency setting.

For long sandbox work, start `merv-runs-wait --url <wait_url>` through Hermes'
background terminal with completion notification enabled. When it exits,
re-read `sandbox.runs`; the watcher wakes the agent but is not the source of
truth. If the watcher is killed or returns no sentinel, read `sandbox.runs`
once and re-arm it rather than assuming the workload stopped.

## Use with the local agent runner

```bash
merv-client agent hermes --enable --command hermes
# Optional model override:
merv-client agent hermes --model anthropic/claude-opus-4-6
```

The runner invokes `hermes -z <instruction>`. Hermes does not currently expose
a per-run MCP configuration flag, so claimed sessions use the scoped
`merv-client call` fallback already included in their instruction. The runner
scrubs ambient `MERV_*` credentials and gives the child only its short-lived
`MERV_AGENT_SESSION_KEY`; it does not pass that credential on argv. Normal
Hermes model, provider, profile, and skill configuration remains available.
This isolates Merv workflow credentials; it is not an OS sandbox and does not
hide the user's model-provider credentials from the Hermes process.

Hermes scripted mode accepts its prompt only as the `-z` argument, so the work
instruction is visible in the local process list even though it contains no
Merv credential. Use separate OS identities or containers when same-machine
research context itself requires isolation.
