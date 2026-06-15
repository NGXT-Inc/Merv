# Notebook CLI Implementation Plan

This plan defines the minimum native notebook support needed to give experiment
agents a short, reliable Python feedback loop without building a custom kernel.

## Goal

Give the agent a notebook operating surface for Python-heavy experiments:

- one mini decision per executable cell
- immediate feedback from each cell
- persistent in-memory Python state across cells
- standard `.ipynb` files visible in JupyterLab
- deterministic clean reruns before using a notebook as evidence

The implementation should reuse standard Jupyter components. It should not
introduce a custom kernel, Ray runtime, SQLite notebook persistence, Marimo, or
a separate notebook format.

## Core shape

Use the standard Jupyter kernel as the long-lived stateful process. Do not add a
notebook daemon in v1 unless the daemonless design proves insufficient.

```text
Agent over SSH or local shell
  -> rp-notebook CLI
    -> standard ipykernel through jupyter_client connection files
    -> standard .ipynb through nbformat

User
  -> JupyterLab dashboard
    -> same .ipynb files under $RP_NOTEBOOK_DIR
```

The CLI owns the agent-facing operations. JupyterLab owns the user-facing visual
surface. Both operate on normal Jupyter notebooks.

## Lessons from LDIA

Keep:

- File-path identity: the notebook id is the canonical path relative to
  `$RP_NOTEBOOK_DIR`.
- Cell-level agent operations: append/execute and update/execute are the right
  abstractions.
- Notebook state summarization: the agent should read compressed Markdown, not
  raw `.ipynb` JSON.
- Rich output preservation: stdout is not enough; tables, HTML, images, results,
  and tracebacks should be saved in standard notebook outputs.
- Staleness awareness: when an earlier cell changes, later executed cells and
  outputs become suspect until rerun.

Avoid:

- Ray actors.
- SQLite notebook persistence.
- Custom notebook frontend state as a prerequisite.
- Marimo or other non-Jupyter notebook runtimes.
- A custom Python kernel.
- A daemon as the first implementation unless connection-file reuse fails.

## Components

### `rp-notebook`

The agent-facing CLI. It manages notebook files and standard Jupyter kernel
connection files directly.

Initial commands:

```sh
rp-notebook new notebooks/explore.ipynb
rp-notebook run-cell notebooks/explore.ipynb --desc "Load data" --file cell.py
rp-notebook update-cell notebooks/explore.ipynb 3 --desc "Fix target column" --file fixed.py
rp-notebook state notebooks/explore.ipynb --max-tokens 6000
rp-notebook reset notebooks/explore.ipynb
rp-notebook list
```

Responsibilities:

- Create/load standard `.ipynb` files with `nbformat`.
- Keep one standard `ipykernel` session per notebook path by storing Jupyter
  connection metadata under `$RP_DASH_DIR/notebook-sessions/<path-hash>/`.
- Reconnect to the live kernel on each CLI call using `jupyter_client`.
- Execute one cell at a time.
- Append or update the executed cell in the notebook.
- Capture standard Jupyter outputs: streams, display data, execute results,
  errors, images, and HTML.
- Save atomically and keep the notebook valid.
- Print concise agent-readable status.

Example output:

```text
notebook: notebooks/explore.ipynb
cell_index: 3
status: ok
stdout:
Train: (8000, 42), Val: (2000, 42)
rich_outputs:
- text/html table saved in notebook
```

### `rp-run-notebook`

A deterministic final runner:

```sh
rp-run-notebook notebooks/explore.ipynb
```

This restarts from a clean kernel and executes the whole notebook top-to-bottom.
Use it before registering a notebook as result evidence.

Implementation can use `nbclient` first. `nbconvert` is useful for later export
support but does not need to be in the minimum loop.

### Optional later: `rp-notebookd`

If connection-file reuse is not reliable enough, add a lazy local daemon later.
If introduced, it should be Unix-socket-only, tokenless only because the socket
is `0600`, versioned, health-checked, disposable, and should hold no durable
notebook state beyond kernel/session metadata.

## Notebook file model

Use only standard Jupyter notebooks:

- `nbformat` for load/save.
- `ipykernel` for execution.
- `jupyter_client` for incremental execution.
- `nbclient` for full clean reruns.

Optional metadata should be small and standards-compatible:

```json
{
  "research_plugin": {
    "cell_description": "Load data",
    "created_by": "rp-notebook",
    "stale": false
  }
}
```

The notebook must remain openable and editable in JupyterLab.

## Kernel state model

The hard invariant is that the live kernel and the notebook file can diverge.
The CLI must make that visible instead of pretending the notebook is always
clean.

Track lightweight session state per notebook:

```json
{
  "kernel_alive": true,
  "kernel_session_id": "...",
  "notebook_hash_at_last_exec": "...",
  "dirty_from_cell": null,
  "last_clean_run": "..."
}
```

Rules:

- `run-cell` appends a new cell and executes it against the current live kernel.
- `update-cell` updates and executes that cell, then marks later executed cells
  as stale.
- If the kernel dies, the next command reports that live Python state was lost.
- `reset` kills the current kernel, starts a fresh one, and marks prior executed
  state as not live.
- `rp-run-notebook` clears staleness only when the full clean rerun succeeds.

## Agent memory model

The agent should not read raw notebook JSON. It should call:

```sh
rp-notebook state notebooks/explore.ipynb --max-tokens 6000
```

The state command should return compressed Markdown:

````markdown
## notebooks/explore.ipynb

[0] ok - Imports

```python
import pandas as pd
...
```

[1] ok - Load data
stdout: Shape: (10000, 43)

[2] stale - Train baseline
Reason: earlier cell 1 was edited after this output was produced.

[3] error - Feature selection
Error: KeyError: 'target'
````

Compression strategy:

- Show recent cells in fuller detail.
- Truncate large stdout/stderr.
- Summarize older cells by index, status, and description.
- Render rich outputs as markers unless they are small text outputs.
- Always show errors with enough traceback detail to support repair.
- Redact known secret values from CLI/state output where possible.

## JupyterLab compatibility

JupyterLab should open the same files from:

```text
$RP_EXPERIMENT_DIR/notebooks/
```

For v1, the agent owns execution and JupyterLab is primarily a visual/readable
surface for the user. Manual concurrent editing should be discouraged until
conflict handling exists.

Still, the CLI should defend against basic races:

- Key advisory locks by canonical relative notebook path hash, not basename.
- Include PID, host, start time, and command in the lock file.
- Recover stale locks only when the owning process is gone or the lock is older
  than a conservative timeout.
- Reload the notebook before each write.
- Record the pre-write file hash and refuse to overwrite if the file changed
  during execution.
- Save to a temporary file in the same directory, validate with `nbformat`,
  flush/fsync, then `os.replace`.

## Path and environment model

Constrain notebooks to `$RP_NOTEBOOK_DIR`.

Defaults:

```sh
RP_EXPERIMENT_DIR="${RP_EXPERIMENT_DIR:-$PWD}"
RP_NOTEBOOK_DIR="${RP_NOTEBOOK_DIR:-$RP_EXPERIMENT_DIR/notebooks}"
RP_DASH_DIR="${RP_DASH_DIR:-$RP_EXPERIMENT_DIR/.rp_dash}"
RP_KERNEL_PYTHON="${RP_KERNEL_PYTHON:-python3}"
```

Rules:

- Resolve paths against `$RP_NOTEBOOK_DIR`.
- Reject `..`, symlink escapes, and absolute paths outside `$RP_NOTEBOOK_DIR`.
- Use canonical relative paths for stable local/sandbox identity.
- Launch kernels from `$RP_EXPERIMENT_DIR`.
- Use `$RP_KERNEL_PYTHON` so the kernel uses the experiment environment, not
  accidentally the plugin daemon environment.

This keeps local and sandbox usage the same:

```sh
rp-notebook run-cell notebooks/explore.ipynb --desc "Inspect data" --file cell.py
```

## Output and timeout model

The minimum executor must handle:

- stdout/stderr streams
- Python exceptions and tracebacks
- PNG/JPEG/SVG images
- HTML/table output
- `execute_result`
- large output caps
- cells that ask for `input()`
- timeout and interrupt

Defaults:

- Cap CLI-printed output aggressively.
- Preserve standard rich outputs in the notebook unless they exceed a hard size
  limit.
- Mark oversized outputs as truncated and tell the agent where the full output
  lives if it was saved separately.
- Treat `input()` prompts as errors; agents should not rely on interactive
  stdin.
- On timeout, interrupt the kernel and report whether the cell is still running,
  was interrupted, or left the kernel in an unknown state.

## Sandbox wiring

Core CLI dependencies:

```text
ipykernel
jupyter_client
nbformat
nbclient
```

Dashboard/export dependencies:

```text
jupyterlab
nbconvert
```

Set:

```sh
RP_NOTEBOOK_DIR="$RP_EXPERIMENT_DIR/notebooks"
RP_KERNEL_PYTHON="${RP_KERNEL_PYTHON:-python3}"
```

Start:

- MLflow on port `5000`
- TensorBoard on port `6006`
- JupyterLab on port `8888`

Do not require a notebook daemon in v1.

## Agent guidance

The skill and sandbox hints should tell the agent:

- Use `rp-notebook` for Python-heavy experiment work.
- Put one mini decision in each cell.
- Execute each cell immediately.
- Fix failed cells with `update-cell`; do not append duplicate debug cells.
- After editing an earlier cell, treat later cells as stale until rerun.
- Use long scripts only for long final jobs or deterministic batch execution.
- Use `rp-run-notebook` before submitting a notebook as evidence.
- Save key metrics and figures outside notebook outputs under `results/` and
  `figures/`.

## Minimum test plan

High-priority tests:

- Two concurrent `run-cell` calls on one notebook serialize without corruption.
- A file change during execution is detected and not overwritten silently.
- Crash during save leaves the previous notebook valid.
- Stale lock recovery works.
- Kernel death reports lost live state.
- CLI restart reconnects to a live kernel or fails clearly.
- `update-cell` on an earlier cell marks downstream cells stale.
- Infinite loop timeout interrupts or reports unknown state clearly.
- `input()` fails clearly.
- Long stdout is capped in CLI output.
- Traceback, PNG, HTML, and `execute_result` outputs are saved in the notebook.
- Paths with spaces, duplicate basenames, symlinks, `..`, and outside absolute
  paths behave correctly.
- `rp-run-notebook` returns nonzero on failure and saves useful error output.
- Local and sandbox environment defaults produce the same command shape.

## Phasing

1. Build daemonless `rp-notebook` locally with tests for create, run, update,
   state, reset, locking, and staleness.
2. Add `rp-run-notebook` with clean top-to-bottom rerun tests.
3. Add sandbox bootstrap dependencies and environment variables.
4. Add JupyterLab as a sandbox dashboard.
5. Add skill and sandbox hint language.
6. Add reviewer/report guidance for notebook artifacts.
7. Later: product UI preview, live notebook event stream, and optional daemon if
   daemonless kernel reuse proves unreliable.

The first implementation should optimize for the smallest reliable loop:
append/update one cell, execute it in a persistent standard kernel, save a valid
notebook, expose staleness honestly, and return a useful state summary to the
agent.
