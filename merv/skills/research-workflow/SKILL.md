---
name: research-workflow
description: >-
  Use when the agent should operate the Merv workflow: ask MCP for
  status and next action, inspect claims, create or run experiments, submit
  typed artifacts, use MCP-controlled mutations, and launch read-only design
  or experiment reviewers when required.
---

# Research Workflow

Use the Merv MCP server as the authority for research state and
workflow state.

## Core model

- Claim: what we think.
- Experiment: what we try.
- Reflection: what the project has learned across experiments.
- Artifact: a typed document (plan, report, graph, project_graph,
  reflection_lens_doc, reflection_doc, change_spec, or a small `result`
  metrics JSON) submitted to the brain against a workflow target. The
  brain-created metrics exhibit is the system-authored exception.
- Review: read-only design, experiment, reflection, human, or automated
  judgment submitted to MCP.

You may freely work on local repo files. Do not treat those edits as
research-state mutations. A file becomes research state only after you submit
it with `artifact.submit` and run the returned upload command.

## Research process

Experiment workflow:

Plan -> Design Review -> Run Experiment -> Submit Results -> Experiment Review
-> Complete / Update Knowledge

Review loops:

- Design review can send work back to Plan.
- Experiment review can send work back to Run Experiment.
- Experiment review can send work back to Plan if the design itself was flawed.

Project reflection workflow:

Finished Experiments -> Reflection Wave -> Multiple Lens Reflections ->
Project Reflection -> Reflection Review -> Publish Project Logic + Next
Experiment Wave

Review loops:

- Reflection review can send work back to Project Reflection.
- Reflection review can send work back to Reflection Wave if the lens
  reflections need to be redone.

## Project reflection

The project also has a level above experiments: a living project logic graph,
maintained through reflection waves. When `workflow.status_and_next` includes
`project_reflection`, treat it as project-level work and use the
`project-reflection` skill for the reflection workflow.

Reflection drift starts advisory, then becomes a gate. The project is nudged to
reflect after the advisory threshold, but once the hard threshold is reached
(`workflow.status_and_next` reports `experiment_create_blocked`), `experiment.create`
is blocked until a project reflection is published. The published reflection's
reviewed change spec may create the next experiment wave. Claim creation can
still be allowed.

## Literature review

The project keeps one living literature review: a General Summary, dynamic
theme sections (each with a required TLDR), and a derived References list.
Whenever a paper informs a plan, claim, or conclusion: `litreview.cite` it to
the experiment/claim it supports, then make a *targeted* `litreview.edit` —
add or amend the one relevant section, never rewrite the document. Read
`litreview.view` (the TLDR outline) before editing so you know the document's
shape. `workflow.status_and_next` surfaces a soft `litreview` hint when cited
papers have not been worked into the review; it never blocks anything.

## Feed

The feed is your main line to the researcher. It is how they follow the work as
it happens — asynchronously, at a glance, without living in the dashboards or
the experiment table. Treat it like a social feed you author: bring them along
with brief, vivid posts at the moments that matter. Post the thing you looked
at — a labeled chart, a before/after, a tight code excerpt, an arxiv page, or
an interactive embed when the result has an explorable dimension — prose alone
is the fallback for the rare insight with no visible form, not the default.
Posts are short by design (a hard length cap), so each is one sharp idea, not
a paragraph.

Post when the work gets interesting: a result that surprises you, a pivot or a
kill, a bottleneck that finally broke, a dead end worth flagging, a hunch
you'd bet on. During a long-running experiment, bounded `kind="status"`
checkpoints threaded onto the experiment's arc keep spectators following
without breaking the finished-takeaway rule; tool responses may also carry a
one-line `feed_note` pointer worth a glance, never a command to clear. Use the
`feed-posting` skill for handle setup and the full craft — register once with
`feed.register`, then write one-idea posts with `feed.post`. Also check
`feed.list` for researcher reactions and replies on your recent posts, and
reply in a follow-up (`in_reply_to`) when a researcher's question deserves one.
It is never gated and never required — but a quiet feed leaves the researcher
in the dark, so keep it alive. The only things that don't belong are the boring
(a bare "exp done, acc 0.81" the table already shows) and the inflated (hype
you can't back with a number).

## The experiment folder

Every experiment owns exactly one folder: `experiments/<name>/`
(announced by `experiment.create`). There is no folder-materialization tool:
create `experiments/<name>/` yourself before the first write. Everything the
experiment is lives there — plan.md, scripts, configs, results, report.md,
graph.json. Artifact uploads read local files. A sandbox is just an ephemeral
machine you SSH into: fetch code and data on the box, write compact outputs
under `$MERV_EXPERIMENT_DIR`, then pull retained files back with
`sandbox.pull_outputs` before submitting them. Heavy artifacts should go to
durable object storage instead of into the repo.

## Workflow

1. Call `project` with `action: "current"` first. Your key binds one immutable
   project, and `current` returns that bound project — its id is the `project.id`
   field of the result. Learn that id here once, then pass it as `project_id`
   explicitly on every subsequent project-scoped tool. When you need the full
   project picture, call `project` with `action: "overview"` or
   `workflow.status_and_next(project_id)`; both use the same bounded project
   context: latest published reflection, literature General Summary, every
   claim including settled or abandoned ones, and every experiment including
   terminal ones with one status-dependent summary.
2. Ask MCP for `workflow.status_and_next(project_id, experiment_id?)` before
   acting.
3. Identify the claim or experiment being worked on. Before creating a new
   claim, check `project` `action: "overview"` so you do not recreate a
   settled or abandoned one; before creating an experiment, use overview to see
   the siblings (name the contrast with them, and do not recreate a dead one).
4. Follow MCP's `next_action`, allowed actions, blocked actions, and gate state.
5. Use MCP for all claim, experiment, artifact, review, and workflow mutations.
6. Pass `project_id` explicitly on every project-scoped tool — the value you
   learned from `project current`. The gateway requires it and enforces that it
   equals the key's bound project: omitting it raises `project_id is required`,
   and a mismatched id is rejected. The `project` tool is the exception — its
   `current` and `overview` actions resolve the bound project from the key and
   take no `project_id` argument.
7. Edit local files only for implementation, notes, plans, configs, and results.
8. Run lightweight commands locally when safe.
9. For quantitative ML work, follow Quantitative observability whether running
   locally or in a sandbox.
10. For expensive local work, data inspection, data engineering, or GPU work,
   request a sandbox with `sandbox.request` and run commands on it yourself over
   SSH (see Execution environment). Prefer CPU-only sandboxes for data profiling
   and preprocessing unless the specific command needs GPU acceleration.
11. After execution in a sandbox, explicitly pull retained files off the box
    before submitting result artifacts. Use `sandbox.pull_outputs`
    for light files, and storage tools for heavy files.
12. Launch a separate read-only reviewer agent when MCP requires design review or
   experiment review.
13. Make sure the reviewer submits directly to MCP using its review capability.
14. Propose conclusions or claim updates only after required artifacts and reviews exist.

If conversation memory is unclear, call `project` with `action: "current"`
again to re-learn the bound project and its id, then ask MCP for
`workflow.status_and_next(project_id, experiment_id?)`. Do not reconstruct
workflow state from memory.

## Quantitative observability

For quantitative ML work — training, evaluation, sweeps, ablations, or any run
where metrics drive the conclusion — save compact machine-readable results,
plots, and tables under the experiment folder. Keep enough provenance in each
record to identify the run purpose, configuration, dataset/evaluation slice,
seed, and the metric's direction. Do not add expensive lineage machinery unless
the approved plan needs it.

```sh
mkdir -p "$MERV_EXPERIMENT_DIR"/results "$MERV_EXPERIMENT_DIR"/figures
```

Submit `results/*.json`, `results/*.csv`, and the figures used by `report.md`.
Record every attempted seed or configuration, including failures and aborted
runs; never curate the evidence down to only the favorable rows. The report is
the interpretation layer, while the submitted result files are the auditable
numeric record.

## Execution environment

Expensive or isolated work can run in a **cloud sandbox** that you drive directly over
SSH. Once the experiment is `ready_to_run` (or already `running`), generate or
select a caller-owned SSH keypair and call `sandbox.request(project_id,
experiment_id?, instance_type?, region?, gpu?, cpu?, memory?, time_limit?,
public_key, additional?)`, passing only the single-line OpenSSH public key. Keep the private
key local. Follow the returned brain-composed `hint`;
`sandbox.request`/`sandbox.get` are the source of truth for provider selection,
polling, expiry, SSH facts, the remote work folder, and copy-out safety. A
sandbox can also be created unattached and addressed by `sandbox_uid`;
`additional: true` requests another machine instead of reusing an experiment's
attached live sandbox.

Use the smallest viable machine. On fixed-SKU providers such as Lambda Labs or
Thunder Compute, use `sandbox.options` or omit `instance_type` to get the live
machine menu; on Modal, request `gpu`/`cpu`/`memory` directly. If the response
is `needs_selection` or `provisioning`, follow it and poll `sandbox.get` after
`poll_after_seconds`; do not use repeated `sandbox.request` calls as a poll
loop.

When `status` is `running`, construct the SSH invocation from the returned
`ssh.host`, `ssh.port`, and `ssh.user` facts plus the caller-owned private key.
Only host, port, and user are returned; the caller always constructs SSH and
supplies its own private-key path.

**Anything expected to run longer than ~5 minutes goes through `merv_run`** —
never babysit a long command over a foreground SSH channel or poll the
transcript for it. Launch it as
`ssh ... 'merv_run <label> -- <command>'` (e.g. `merv_run seed0 -- python train.py
--seed 0`): the run detaches, survives disconnects, logs to
`.runs/<label>/log.txt`, and writes an `exit_code` sentinel when it finishes.
Then call `sandbox.runs(project_id, experiment_id, wait_seconds=...)`: it
returns the moment a run reaches a terminal state, so the answer arrives within
seconds of the run ending rather than at the next poll. Keep `wait_seconds<=45`
unless your client's tool timeout is known to allow more.

### Arm the watcher first

**After a launch, arming a watcher is the next thing you do** — before more
edits, before another tool call, before ending the turn. A run that ends with
nobody watching bills until you happen to look.

1. Launch with `merv_run <label> -- <command>`.
2. Call `sandbox.runs`. If the new label is not listed yet, call again in a
   short loop — the brain mirrors a receipt up to ~90s after `merv_run` writes
   it, and each call is cheap.
3. Take that row's `wait_url` (present on hosted and local HTTP surfaces).
4. Arm your platform's background watcher on it.
5. Only then continue other work or end the turn.

`merv-runs-wait` — in the plugin bundle's `bin/`, run it by full path if it is
not on your `PATH` — is the watcher. It blocks while the run runs, and its EXIT
is the wake signal: stdout carries exactly one line,
`MERV_RUNS_WAIT <state> <label> [status=... exit_code=...]`, and the exit code
is the state. Heartbeats go to stderr; never read an answer out of them. If the
watcher died with NO final line (killed, interpreter failure), that is
`poll_error` by definition: make one authenticated `sandbox.runs` call to read
truth, then re-arm — never infer an outcome from a silent exit.

- **Claude Code**: run `merv-runs-wait --url <wait_url>` as a background Bash
  task (`run_in_background`). The task's exit re-invokes you, so the turn can
  end immediately instead of holding a long-poll open; this works from
  subagents too. With no bundle on the machine, `curl -N <wait_url>` streams
  the same final line, but the exit codes are curl's rather than the
  contract's — prefer the watcher.
- **Cursor (3.0+)**: run it in a background shell with notify-on-output armed
  on the sentinel regex `^MERV_RUNS_WAIT `. The shell's exit or the matched
  line resumes you without a foreground block. If a long-idle reattach fails,
  re-run the watcher or fall back to a stop-hook loop.
- **Codex CLI**: run it in the foreground blocking terminal (raise
  `background_terminal_max_timeout` in config when holds outlast the default),
  or in a background terminal plus an empty `write_stdin` poll, which unblocks
  the instant the process exits.
- **Kilo**: `background_process` with `ready.pattern` set to
  `^MERV_RUNS_WAIT `, which blocks until the sentinel line arrives.
- **No-shell surfaces** (Claude Desktop and the like): no watcher is possible,
  so loop on `sandbox.runs` with `wait_seconds` yourself and never call tighter
  than 60s apart. Fabricating a completion or abandoning the loop loses the
  run: the box keeps billing and nobody reads the receipts.

When it wakes you, branch on the final line:

| Exit | State | Next action |
|---|---|---|
| 0 | `done` | the observation ended, not necessarily the work — read `status=` and `exit_code=` |
| 2 | `still_running` | the hold cap or `--deadline` elapsed; nothing is wrong — re-arm the same command/URL immediately |
| 3 | `poll_error` | transport or auth hiccup — make ONE authenticated `sandbox.runs` call to read truth, then re-arm |
| 4 | `no_such_run` | keyed mode: absent past registration grace. URL mode: the URL may simply have expired while the run lives on — make ONE authenticated `sandbox.runs` call before concluding anything, and re-check `sandbox_uid` and label against the launch receipt |

Inside `done`:

- `status=finished exit_code=0` — pull outputs and proceed.
- `status=finished` with a nonzero `exit_code` — read the log, fix, relaunch.
- `status=lost` or `status=unknown` — the box died or the observation failed.
  Check `sandbox.get` and decide whether to relaunch; the status table below
  says what each of those does and does not license.

**Never treat `done` as workload success.** Exit 0 says the run reached a
terminal state; only `exit_code=` says the work worked.

**A sandbox bills continuously, so the gap between a run finishing and you next
looking is paid for** — one measured incident burned 62 idle minutes on an H100.
The watcher exists so that YOU close that window: when it wakes you, pull
outputs, launch the next tier, or release when the plan says so. Nothing
releases a box automatically, by design — release stays your explicit decision
under the two-step rule below. Prefer chaining work into one `merv_run` over
leaving a box idle between steps.

When a row carries no `wait_url` (direct or library surfaces that mint no
signed URLs), use keyed mode with `MERV_MCP_KEY` exported — same final line,
same exit codes:

```bash
merv-runs-wait --project-id <project_id> --sandbox-uid <sandbox_uid> \
  --label <label> [--deadline 3600]
```

Read `status`, not just `exit_code`:

| Status | Meaning |
|---|---|
| `finished` | the run ended and `exit_code` says how |
| `running` | still going |
| `lost` | the receipts **were** read on the way down and no sentinel was there |
| `unknown` | the box died before its receipts could be read |

`unknown` is not a failure. The run's outcome is genuinely not known — it may
well have succeeded — so never record it as a failed run. Only `lost` is a
finding. The box is gone, so its logs and unpulled outputs are gone with it;
what survives is whatever you already retained: pulled outputs and submitted
artifacts. Check those. If nothing was retained, you cannot
know how the run went, and the honest move is to re-run it rather than write
down a result you did not observe.

Every sandbox.* response carries a compact `runs` line while runs exist. Labels
are one-shot — pick a new label per launch. Finished-run receipts survive box
death, but logs and outputs do not: pull what you need before release/expiry.

Use `sandbox.terminal(project_id, experiment_id)` to inspect transcript output
and the structured `last_command` status before re-running anything long. If
`command_status_stale` is true, the transcript read failed and `last_command` is
the last successful snapshot, which is still useful for recovery decisions.
If the sandbox died, expired, or the command was interrupted by infrastructure
while the approved plan still stands, call
`experiment.transition(project_id, experiment_id, transition="retry_running", evidence={...})`
before requesting or attaching the replacement sandbox. This keeps the same attempt and
records why execution is being rerun; use a planned retry only when the design
itself needs to change. Keep the rerun's outputs distinct and record why the
original execution was interrupted.

While the sandbox is live, make experiment-folder edits on the VM under
`$MERV_EXPERIMENT_DIR`. No files are copied automatically. Keep datasets, caches,
temporary checkpoints, and other disposable bulk files under `$RP_DATASET_DIR`.
Keep durable scripts, configs, notes, compact outputs, report figures/tables,
and deliberate final artifacts under `$MERV_EXPERIMENT_DIR` so you can pull them
off deliberately before release.

Save compact evidence under `$MERV_EXPERIMENT_DIR` as the run proceeds rather
than depending on transient terminal output.

Before submitting result artifacts, call `sandbox.pull_outputs`
for light retained files. Its inputs select the sandbox and optional relative
`paths`; it takes no `key_path` or `overwrite` arguments. Run the returned rsync
command yourself, replacing its placeholders with your caller-owned private key
and local destination. Upload heavy artifacts with `storage.submit` when durable
storage is enabled — it returns a one-line `curl` command you run to push the
bytes straight to object storage. Artifact uploads read local files, so remote
sandbox paths cannot be submitted until you have pulled the files back locally.
Do this before `sandbox.release`; release and expiry destroy the VM and anything
you did not retain. Release is two-step: the first call returns a retention
checklist without deleting, and only a second call with
`confirm_retained: true` terminates the machine.

Do not embed secrets in commands or retained files. Treat the sandbox as
ephemeral: durable outputs must be explicitly copied or uploaded and then
submitted with `artifact.submit`.

## Experiment creation

Prefer the minimal MCP shape:

```json
{
  "project_id": "proj_...",
  "name": "lora-rank-sweep",
  "intent": "One concise statement of what the experiment will test.",
  "tested_claim_ids": ["claim_..."]
}
```

`project_id` is the id you learned from `project current` (the result's
`project.id`); it is required here as on every project-scoped tool.

`name` is **required**: a short, folder-safe name (letters, digits, `.`, `_`,
`-`; max 48 characters) that becomes the experiment folder
`experiments/<name>/` — everything the experiment is (plan, code, results,
report, graph) lives there, and it is the local destination for retained
sandbox outputs. Sandbox files are not synchronized automatically. Names are
unique within a project: if the name is already taken, creation is rejected and
you must pick a new one.

The create response announces the folder: it includes `folder` (e.g.
`experiments/lora-rank-sweep/`). Create that directory yourself before the
first file write, then work inside it from that moment on — starting with
`plan.md`. There is no `experiment.materialize_folders` tool.

Pick the name for **navigation**: the project supplies the shared context, so
the name should carry only the contrast — lead with what distinguishes this
experiment from its siblings, and do not repeat the project topic. In a LoRA
replication project, `released_adapters` / `scratch_training` /
`paper_only_rebuild` scan instantly; `lora_glue`, `lora_glue_scratch`, and
`lora_glue_paper_only` all read as the same experiment until the last word.

`intent` is the durable **one-line headline** — the experiment's title in the
UI. The full design (hypothesis, method, evaluation, risks) does **not** go in
`intent`; it lives in the `plan.md` artifact (see Experiment plan below). The
MCP server still accepts the older aliases `claim_id`, `claim_ids`, `title`,
`hypothesis`, `design`, `success_criteria`, and `risks`, but they are
deprecated: `title` and friends only backfill an empty `intent` (they are not
concatenated into it) — put that content in the plan instead.
Create always starts at `planned`. Use `experiment.transition` for workflow
state changes.

## Experiment plan

The plan is one repo file in the experiment folder
(`experiments/<name>/plan.md`) submitted with role `plan`. It is the
**face of the experiment**: what the user reads in the UI
and what the design reviewer evaluates. Write it from
`skills/research-workflow/plan-template.md` (a PRD-style template).

The plan has a small **required spine** — `experiment.transition(submit_design)`
is blocked until each of these headings has real content:

- **Summary** — 2–3 plain sentences: what and why (the readable face).
- **Objective & hypothesis** — which claims, expected direction, and why it matters.
- **Evaluation** — how you will judge success: metric(s), baseline, decision
  rule, success threshold, and what would invalidate the result. This is the
  contract the experiment reviewer later grades the conclusion against.

The recommended sections (**Method**, **Outputs**, **Risks & confounders**) are
not lint-enforced, but the design reviewer can return `needs_changes` if they
are missing or too thin for this experiment. Scale their depth to the work.

Plans may include figures: every relative image link must resolve to a local
file under 5 MB. The plan upload response returns one follow-up command per
linked figure — run each verbatim; `submit_design` re-checks that each linked
figure was submitted alongside the plan.

If `submit_design` is rejected for missing sections, fill them in and
**resubmit the plan** (`artifact.submit` with role `plan`, then run the
returned upload command) before retrying — the lint reads the bytes you
SUBMITTED, never the live file, so an edit counts only once it is resubmitted.

## Results report

The report is one repo file in the experiment folder
(`experiments/<name>/report.md`) submitted with role `report`. It is
the **face of the executed experiment**: what the
user reads in the UI once results exist and what the experiment reviewer
grades against the plan's Evaluation section. Write it from
`skills/research-workflow/report-template.md`, in the same pass as your result
files — save the figures (matplotlib PNGs) while the run's metrics are at hand.

`experiment.transition(submit_results)` is blocked until the current attempt
has BOTH a `result` artifact and a `report` artifact whose SUBMITTED content
(the bytes the upload pinned) passes the report lint:

- **Summary**, **Results**, **Deviations from plan**, **Conclusion** headings
  with real content.
- **Results must reference and interpret the submitted result evidence** using
  the exact metrics and decision rule named by the plan's Evaluation section.
- **Under 16 KB.** The report is the executive layer: link raw metrics files
  (`results.json`, logs) as separate result artifacts instead of inlining.
- **Every relative image link has submitted figure content.** Save figures
  next to the report (`figures/*.png`), copy them off the sandbox so they exist
  locally, and THEN submit the report — the upload response returns one
  follow-up command per linked figure; run each verbatim. Added a figure
  later? Resubmit the report.

The Conclusion must apply the plan's pre-registered decision rule explicitly —
the experiment reviewer compares the two documents side by side.

## Logic graph

The logic graph is one JSON repo file in the experiment folder
(`experiments/<name>/graph.json`) submitted with role `graph`. It is a
**qualitative story you write about the logical path of the experiment** —
the critical questions that needed answers, the hard decisions and the
reasoning behind them, the pivots (including those forced by reviews), and
what was learned — a small DAG the user explores in the UI during and after
the run. Write it from `skills/research-workflow/graph-template.md`.

This is not an event-driven graph. Events may be mentioned as anchors for
reasoning, but the structure is logic: question → decision → consequence →
lesson. It is NOT a pipeline or provenance diagram — if your nodes are
components and your edges read `produces`/`contains`/`records`, you have
drawn dataflow, not the story. And it is not a generated artifact: do not
build it with a script over your result files; choosing what mattered is the
authorship, so write the JSON yourself.

You design the graph. Node `kind` names, edge labels, and structure are yours;
the template's vocabulary is illustrative, not required. What deserves a node
is an editorial call — record what shaped the experiment, not every step. If a
development adds no valuable information to the story, you may leave it out.

Keep nodes brief and use `refs` for depth: a node's `refs` array takes
record ids (`art_…`, `rev_…`, `claim_…`, `exp_…`), and the UI resolves them
into links the user and reviewer can follow. Point a problem node at the
review that forced a pivot or the submitted artifact that shows an outcome —
instead of restating their contents in `detail`.

`experiment.transition(submit_results)` is blocked until the current attempt
has a role-`graph` artifact whose SUBMITTED content passes the envelope lint: valid
JSON (`version: 1`), every node with a unique `id` and non-empty `label`,
**at most 16 nodes**, edges referencing existing nodes and forming a DAG, file
under 16 KB. The lint checks shape only; the experiment reviewer judges
whether the story is honest and consistent with the report and transcript.

Start the graph early and keep a local copy current as the story develops —
the user watches it live, and a hard decision is best recorded in the moment
you make it, while the reasoning is fresh; a graph reconstructed at the end
keeps the events but loses the *why*. After a review rejection, consider
whether the rejection and the rework it forces belong in the story. If the
graph is at the 16-node budget and something important must be added, reduce
the graph first; how to retell the story within the budget is your call.

## Artifact submission

Artifacts are typed documents; only the mandated roles exist (plan, report,
graph, project_graph, reflection_lens_doc, reflection_doc, change_spec, and
the small `result` metrics JSON). The brain-created metrics exhibit is not
something the agent submits.

To submit a gated document or result file:

- write the file locally FIRST (if the experiment ran in a sandbox, pull
  retained files off the box with `sandbox.pull_outputs` first; uploads read
  local files and cannot reach remote sandbox paths)
- call `artifact.submit {project_id, target_type, target_id, role, path}` with
  the file's relative path — pass `lens_id` when the role is
  `reflection_lens_doc`, and ensure that lens Markdown has a non-empty
  `## Summary` for its macro TLDR — and
  run the returned one-line upload command **verbatim** (one-time token,
  expires in ~15 min)
- for markdown with relative image links, the upload response returns one
  follow-up command per figure; run each the same way
- when `workflow.status_and_next` includes `artifact_guidance`, follow its
  `role`; do not guess plural role names such as `results`,
  `reports`, or `output` (the singular roles are `result`, `report`, and
  `graph`)
- gates and lints judge the SUBMITTED bytes (pinned at upload), never the live
  working tree: after fixing a gated artifact, resubmit it — editing the file
  alone changes nothing the workflow can see; a resubmit replaces the previous
  artifact in the same slot
- do not create artifact manifests or content-addressed objects yourself
- there is no version history to restore through MCP; edit the live file
  normally and resubmit it

### Batch reads and deeper dives

At project scope, `project(action="overview")`,
`workflow.status_and_next(project_id).context`, and
`review.start(...).project_context` use the same five-section macro packet.
It names every claim and experiment but references only the latest published
reflection document and project graph. Use an experiment id to enter its
four-section context; do not expect project context to enumerate every file.

Inside an experiment, `workflow.status_and_next(project_id, experiment_id)` is
the one context read. Its `context` has exactly four sections: experiment,
latest plan, latest report, and the other current-attempt artifacts. A live
experiment gets the full latest plan; a terminal experiment gets the plan's
Summary; the latest report is full when present. Every artifact reference has
an id, local path, and submission timestamp.

Transitions return only a compact acknowledgement. After a transition, call
`workflow.status_and_next` again when you need the next instruction or refreshed
context. Do not look for experiment state in the transition response.

`artifact.find(artifact_ids=[...])` likewise batches up to 50 plan/report
or other artifact ids in first-seen order. It returns slim metadata by default.
Use ids from the context's artifact list. Only when the macro context cannot
answer the question, add
`include_content=true`; bounded textual submissions arrive in each artifact's
`content` envelope, while binary or unavailable bytes remain marked and are not
injected as text. A missing id fails the whole batch, so use ids from the same
project and current authoritative state.
This plural-id interface is intentionally artifact-only. Do not call or pass
plural ids to the internal compatibility reader `experiment.get_state`.

## Review discipline

When `workflow.status_and_next` says to launch or wait for a reviewer, follow
`workflow.review_gate`. If no request exists, call `review.request` with the
exact target and role: `target_type: "experiment"`, the experiment's
`target_id`, `role: "design_reviewer" | "experiment_reviewer"`, and your own
`producer_session_id` (plus optional `reason`). Launch a separate reviewer with
the returned `reviewer_handoff` — its `spawn_prompt` is a ready-made prompt for
the reviewer subagent — and `reviewer_capability`. Reflection review instead
uses `target_type: "reflection"` and `role: "reflection_reviewer"` as described
by the `project-reflection` skill.

The capability plaintext is returned only in the `review.request` response, so
retain it long enough to hand off. It is not consumed by `review.start`: it
remains usable to start a reviewer session while that request is still open.
Do not issue a replacement request merely because a session started, because a
new request supersedes the existing one. Request a fresh capability only when
the prior plaintext was lost, expired, superseded, or the target snapshot
changed.

Reviewer agents must be separate and operate read-only by procedure. They call
`review.start` with exactly `review_request_id`, `reviewer_capability`, their
own `caller_session_id`, and optional `declared_agent`, then submit with the
returned `review_session_id`. The start capability and submit session gate the
review protocol, but the server does not authenticate unrelated tool calls as
reviewer calls. Therefore reviewers must not mutate claims, experiments,
artifacts, sandboxes, or workflow state. Their `caller_session_id` is required
and must never be the producer session's.

After any review submits, call `workflow.status_and_next` again. MCP's
`revision_context`, experiment state, and allowed actions determine the next
step.

## Completion

Before marking an experiment complete:

- the required artifacts are submitted
- design and experiment reviews are recorded and accepted by MCP
- conclusion is grounded in files or sandbox outputs
- MCP accepts the transition

If MCP rejects a mutation, follow its `next_action` rather than working around it.
